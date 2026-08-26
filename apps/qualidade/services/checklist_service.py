"""
O checklist de qualidade: montar a lista, preencher, e deixar o laudo decidir.

DUAS COISAS SEPARADAS, e é a separação que faz o módulo funcionar:

  · O CADASTRO diz o que conferir. `ParametroQualidadeProduto` já existia, com
    produto, etapa, tipo de valor, unidade e faixa. Tem tela e replicação
    entre filiais. Nada disso muda aqui.

  · O LAUDO diz o que se achou. Era um JSON `{"brix": 12.5}` -- valores
    soltos, sem veredito por linha e sem memória do que era exigido. Um pH
    esquecido não virava pendência: virava chave ausente, e o laudo fechava
    aprovado sem ele.

MONTAR VEM ANTES DE PREENCHER. O checklist nasce inteiro, com todas as linhas
do cadastro em PENDENTE, e só depois recebe valores. É o contrário de deixar a
pessoa digitar o que lembrar: item não medido fica visível como não medido, e
obrigatório em branco impede o laudo de fechar.

O RESULTADO É CALCULADO, NÃO INFORMADO. `AnaliseQualidadeService.registrar`
recebe `resultado` como argumento -- quem chama decide, e um laudo com Brix
fora da faixa podia ser gravado como aprovado sem nada reclamar. Aqui o
veredito sobe dos itens: um não conforme reprova o laudo, e é o laudo que
bloqueia o lote.

O QUE O NÚMERO NÃO DECIDE, A PESSOA DECIDE. Aparência, odor e textura não têm
mínimo e máximo. Para eles o veredito é de quem conferiu -- um "conforme"
automático em campo subjetivo seria um carimbo que ninguém deu.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.qualidade.constants.enums import ResultadoAnalise, TipoAnalise
from apps.qualidade.models import (
    AnaliseQualidade, ItemAnalise, ParametroQualidadeProduto,
)

SITUACAO = ItemAnalise.Situacao

# As três listas da especificação são etapas do `TipoAnalise` que já existe.
# Escrever o mapa aqui, e não em cada tela, é o que impede "recebimento" virar
# `materia_prima` numa tela e `processo` noutra.
ETAPA_RECEBIMENTO = TipoAnalise.MATERIA_PRIMA
ETAPA_PRODUCAO = TipoAnalise.PROCESSO
ETAPA_ACABADO = TipoAnalise.PRODUTO_ACABADO


class ChecklistService:

    # ── Montar ───────────────────────────────────────────────────────────

    @staticmethod
    def parametros_de(filial, produto, etapa) -> list:
        """O que o cadastro manda conferir neste produto, nesta etapa."""
        return list(
            ParametroQualidadeProduto.objects
            .for_filial(filial)
            .filter(produto=produto, etapa=etapa, ativo=True)
            .order_by('nome_parametro')
        )

    @classmethod
    @transaction.atomic
    def abrir(cls, filial, produto, etapa, responsavel,
              lote=None, ordem_producao=None) -> AnaliseQualidade:
        """
        Abre a análise com o checklist inteiro, em pendente.

        NASCE PENDENTE, e não vazia: a lista completa na tela é o que mostra o
        que falta conferir. Uma análise que só ganha linha quando alguém digita
        deixa o esquecido invisível — e o esquecido é o pH que ninguém mediu.

        Sem parâmetros cadastrados não abre. Um laudo sem nada a conferir não é
        um laudo aprovado, é um cadastro que falta — e deixar passar produziria
        um "aprovado" que não olhou para nada.
        """
        parametros = cls.parametros_de(filial, produto, etapa)
        if not parametros:
            raise DomainError(
                f'Não há parâmetros de qualidade cadastrados para '
                f'{produto} nesta etapa. Cadastre-os antes de abrir a análise.'
            )

        analise = AnaliseQualidade.objects.create(
            filial=filial, lote=lote, ordem_producao=ordem_producao,
            tipo_analise=etapa, parametros={},
            resultado=ResultadoAnalise.PENDENTE,
            responsavel_tecnico=responsavel,
            data_analise=timezone.now(),
        )
        ItemAnalise.objects.bulk_create([
            ItemAnalise(
                analise=analise, parametro=parametro,
                nome_parametro=parametro.nome_parametro,
                unidade_medida=parametro.unidade_medida,
                valor_minimo=parametro.valor_minimo,
                valor_maximo=parametro.valor_maximo,
                obrigatorio=parametro.obrigatorio,
                ordem=indice,
            )
            for indice, parametro in enumerate(parametros)
        ])
        return analise

    # ── Preencher ────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def preencher(cls, analise: AnaliseQualidade, respostas: dict,
                  usuario=None) -> list[ItemAnalise]:
        """
        Grava as respostas. `respostas` é `{item_id: {...}}`.

        Item AUSENTE do dicionário fica como está — a tela manda um formulário
        por vez, e tratar ausência como "limpar" apagaria o que já foi
        conferido nas outras linhas.

        A ação corretiva carimba QUEM e QUANDO no momento em que é escrita.
        Guardar só o texto deixaria a auditoria com uma frase sem dono.
        """
        alterados = []
        for item in analise.itens.all():
            dados = respostas.get(item.pk) or respostas.get(str(item.pk))
            if dados is None:
                continue

            if 'valor' in dados:
                cls._guardar_valor(item, dados['valor'])
            if 'situacao' in dados and dados['situacao'] in SITUACAO.values:
                item.situacao = dados['situacao']
            else:
                # O número decide sozinho quando há faixa; fora disso mantém o
                # que a pessoa marcou.
                item.situacao = item.avaliar()
            if 'observacao' in dados:
                item.observacao = (dados['observacao'] or '')[:300]

            cls.registrar_acao(
                item, dados.get('acao_corretiva'), usuario, salvar=False,
            )

            item.save()
            alterados.append(item)

        cls._espelhar_no_json(analise)
        return alterados

    @staticmethod
    def registrar_acao(item: ItemAnalise, texto, usuario=None,
                       salvar: bool = True) -> bool:
        """
        Escreve a ação corretiva de um desvio, carimbando QUEM e QUANDO.

        EXTRAÍDO DE `preencher` PARA TER UM DONO SÓ. A tela de não
        conformidades registra a ação item a item, sem passar pelo checklist
        inteiro; copiar as três atribuições lá faria existirem duas definições
        de "registrar ação" — e no dia em que uma ganhasse, por exemplo, um
        campo de prazo, a outra continuaria gravando frase sem dono.

        Guardar só o texto deixaria a auditoria com uma frase sem responsável,
        que é o primeiro registro que a fiscalização pede.

        TEXTO IGUAL AO QUE JÁ ESTÁ NÃO RECARIMBA. Reabrir a tela e salvar sem
        mudar nada moveria a data da ação para hoje, e a auditoria perderia
        quando o desvio foi de fato tratado.
        """
        acao = (texto or '').strip()
        if not acao or acao == item.acao_corretiva:
            return False
        item.acao_corretiva = acao[:500]
        item.acao_responsavel = usuario
        item.acao_em = timezone.now()
        if salvar:
            item.save(update_fields=[
                'acao_corretiva', 'acao_responsavel', 'acao_em',
            ])
        return True

    @staticmethod
    def _guardar_valor(item: ItemAnalise, bruto) -> None:
        """
        Número no campo de número, texto no de texto.

        Campo vazio LIMPA os dois e devolve o item a pendente: "apaguei o que
        tinha digitado" e "conferi e está conforme" não podem terminar iguais.
        """
        texto = str(bruto).strip() if bruto is not None else ''
        if not texto:
            item.valor_numero = None
            item.valor_texto = ''
            item.situacao = SITUACAO.PENDENTE
            return
        try:
            item.valor_numero = Decimal(texto.replace(',', '.'))
            item.valor_texto = ''
        except (InvalidOperation, ValueError):
            item.valor_numero = None
            item.valor_texto = texto[:160]

    @staticmethod
    def _espelhar_no_json(analise: AnaliseQualidade) -> None:
        """
        Mantém `AnaliseQualidade.parametros` em dia com os itens.

        O campo é lido por código que existia antes desta tabela (laudo, API,
        relatório). Deixá-lo parado faria as duas leituras discordarem — e a
        antiga é a que já está em uso.
        """
        analise.parametros = {
            item.nome_parametro: (
                float(item.valor_numero) if item.valor_numero is not None
                else item.valor_texto
            )
            for item in analise.itens.all()
            if item.preenchido
        }
        analise.save(update_fields=['parametros'])

    # ── Fechar ───────────────────────────────────────────────────────────

    @staticmethod
    def pendencias(analise: AnaliseQualidade) -> list[ItemAnalise]:
        """Os obrigatórios que ainda não foram conferidos."""
        return [i for i in analise.itens.all() if i.pendente_obrigatorio]

    @classmethod
    @transaction.atomic
    def concluir(cls, analise: AnaliseQualidade, usuario=None,
                 acao_reprovacao: str = '', observacao: str = '') -> AnaliseQualidade:
        """
        Fecha o laudo com o resultado que SOBE DOS ITENS.

        Um não conforme reprova. Não é média nem ponderação: em alimento, um
        parâmetro fora da faixa é o parâmetro que importa, e diluí-lo entre
        nove conformes produziria um "aprovado" que ninguém sustenta.

        Obrigatório em branco IMPEDE o fecho. Fechar com pendência é assinar
        que se conferiu o que não se conferiu.

        `Aprovado com ressalva` continua sendo decisão de gente, e por isso é
        passado de fora: é o caso em que a qualidade aceita conscientemente um
        desvio, e nenhuma regra automática deveria poder concedê-lo.
        """
        from apps.qualidade.services.analise_service import AnaliseQualidadeService

        faltando = cls.pendencias(analise)
        if faltando:
            nomes = ', '.join(i.nome_parametro for i in faltando)
            raise DomainError(
                f'Faltam parâmetros obrigatórios para fechar o laudo: {nomes}.'
            )

        reprovou = analise.itens.filter(situacao=SITUACAO.NAO_CONFORME).exists()
        if reprovou:
            return AnaliseQualidadeService.reprovar(
                analise,
                acao=acao_reprovacao or analise.acao_reprovacao or 'bloqueio',
                motivo=observacao or cls._motivo_automatico(analise),
            )

        if observacao:
            analise.observacao = observacao
        if usuario is not None:
            analise.responsavel_tecnico = usuario
        analise.data_analise = timezone.now()
        analise.save(update_fields=[
            'observacao', 'responsavel_tecnico', 'data_analise',
        ])
        return AnaliseQualidadeService.aprovar(analise)

    @staticmethod
    def _motivo_automatico(analise: AnaliseQualidade) -> str:
        """
        Diz QUAIS itens reprovaram, e não só que reprovou.

        O motivo vai parar em `LoteProduto.motivo_bloqueio`, que é o texto que
        alguém lê meses depois ao esbarrar num lote travado na câmara. "Análise
        reprovada" não diz o que fazer; "Brix 8 (mín. 11)" diz.
        """
        partes = []
        for item in analise.itens.filter(situacao=SITUACAO.NAO_CONFORME):
            valor = item.valor
            faixa = f' ({item.faixa})' if item.faixa else ''
            partes.append(f'{item.nome_parametro}: {valor}{faixa}')
        return 'Não conforme — ' + '; '.join(partes) if partes else ''

    # ── Leitura para a tela ──────────────────────────────────────────────

    @staticmethod
    def resumo(analise: AnaliseQualidade) -> dict:
        itens = list(analise.itens.all())
        nao_conformes = [i for i in itens if i.situacao == SITUACAO.NAO_CONFORME]
        return {
            'total': len(itens),
            'conformes': sum(1 for i in itens if i.situacao == SITUACAO.CONFORME),
            'nao_conformes': len(nao_conformes),
            'pendentes': sum(1 for i in itens if i.situacao == SITUACAO.PENDENTE),
            'obrigatorios_pendentes': sum(1 for i in itens if i.pendente_obrigatorio),
            'sem_acao': [
                i for i in nao_conformes if not i.acao_corretiva.strip()
            ],
        }
