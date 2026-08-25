"""
As contas da receita: participação, rendimento, perda e custo.

TODAS AS CONTAS NUM LUGAR SÓ. Custo por kg aparece na receita, na ordem de
produção, na formação de preço e no relatório de margem — quatro contas
iguais espalhadas divergem na primeira mudança de regra, e aí ninguém sabe
qual das quatro está certa.

O QUE ENTRA NO CUSTO, e por quê:

  · MATÉRIA-PRIMA pelo custo médio do produto, com a PERDA PREVISTA por
    dentro. Comprar 1.000 kg de manga para produzir 600 kg de polpa não
    custa "1.000 kg de manga": custa o que foi comprado, e o custo inteiro
    fica em cima do que sobrou. Ignorar a perda subestima o custo em 40%
    justamente no produto que dá menos margem;

  · EMBALAGEM SEPARADA da matéria-prima, e não somada dentro dela. São
    decisões de compra diferentes, negociadas com fornecedores diferentes,
    e quando o custo sobe a primeira pergunta é qual dos dois subiu;

  · MÃO DE OBRA E INDIRETO vêm da ficha do ERP, onde já estavam.

O RENDIMENTO REAL SAI DAS ORDENS, não de um campo digitado. Campo digitado
é lembrança; ordem encerrada é o que a fábrica fez. Sem nenhuma ordem
encerrada, o serviço devolve `None` -- e a tela diz "ainda não produzido",
em vez de mostrar zero, que seria lido como rendimento péssimo.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.polpa.models import EtapaReceita, FichaProduto, Receita
from apps.producao.models import FichaTecnica, ItemFichaTecnica, OrdemProducao

ZERO = Decimal('0')
CEM = Decimal('100')
CENTAVO = Decimal('0.0001')


class ReceitaService:

    # ── Criação e versões ────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def criar(filial, produto, dados: dict) -> Receita:
        """Abre a receita e a ficha técnica do ERP juntas."""
        ficha = FichaTecnica.objects.create(
            filial=filial,
            produto_acabado=produto,
            descricao=dados.get('descricao') or str(produto),
            codigo=dados.get('codigo') or '',
            versao=dados.get('versao') or '1.0',
            quantidade_produzida=dados.get('quantidade_produzida') or 1,
            tempo_producao_minutos=dados.get('tempo_producao_minutos') or 0,
            custo_mao_obra_padrao=dados.get('custo_mao_obra_padrao') or 0,
            custo_indireto_padrao=dados.get('custo_indireto_padrao') or 0,
            observacao=dados.get('observacao') or '',
            status=FichaTecnica.Status.RASCUNHO,
        )
        return Receita.objects.create(
            filial=filial, ficha=ficha,
            rendimento_esperado=dados.get('rendimento_esperado'),
            temperatura_processo_min=dados.get('temperatura_processo_min'),
            temperatura_processo_max=dados.get('temperatura_processo_max'),
            observacoes_tecnicas=dados.get('observacoes_tecnicas') or '',
        )

    @staticmethod
    @transaction.atomic
    def nova_versao(receita: Receita, versao: str = '') -> Receita:
        """
        Copia a receita inteira numa versão nova, em rascunho.

        MUDAR A RECEITA QUE JÁ PRODUZIU seria apagar a explicação dos lotes
        que saíram dela: a fórmula de hoje diria uma coisa e o produto na
        câmara seria outro. A cópia leva itens e etapas — recomeçar do zero
        faria a pessoa editar a ativa "só desta vez".
        """
        nova = versao.strip() or ReceitaService._proxima_versao(receita)
        if FichaTecnica.objects.filter(
            filial=receita.filial, produto_acabado=receita.produto, versao=nova,
        ).exists():
            raise DomainError(f'Já existe a versão {nova} deste produto.')

        origem = receita.ficha
        ficha = FichaTecnica.objects.create(
            filial=receita.filial,
            produto_acabado=origem.produto_acabado,
            descricao=origem.descricao,
            codigo=origem.codigo,
            versao=nova,
            quantidade_produzida=origem.quantidade_produzida,
            tempo_producao_minutos=origem.tempo_producao_minutos,
            custo_mao_obra_padrao=origem.custo_mao_obra_padrao,
            custo_indireto_padrao=origem.custo_indireto_padrao,
            observacao=origem.observacao,
            status=FichaTecnica.Status.RASCUNHO,
        )
        ItemFichaTecnica.objects.bulk_create([
            ItemFichaTecnica(
                ficha=ficha, materia_prima=item.materia_prima,
                quantidade=item.quantidade, perda_prevista=item.perda_prevista,
                observacao=item.observacao,
            )
            for item in origem.itens.all()
        ])

        copia = Receita.objects.create(
            filial=receita.filial, ficha=ficha,
            rendimento_esperado=receita.rendimento_esperado,
            temperatura_processo_min=receita.temperatura_processo_min,
            temperatura_processo_max=receita.temperatura_processo_max,
            observacoes_tecnicas=receita.observacoes_tecnicas,
        )
        EtapaReceita.objects.bulk_create([
            EtapaReceita(
                receita=copia, ordem=etapa.ordem, nome=etapa.nome,
                equipamento=etapa.equipamento, tempo_minutos=etapa.tempo_minutos,
                temperatura_min=etapa.temperatura_min,
                temperatura_max=etapa.temperatura_max,
                perda_percentual=etapa.perda_percentual,
                instrucao=etapa.instrucao,
            )
            for etapa in receita.etapas.all()
        ])
        return copia

    @staticmethod
    def _proxima_versao(receita: Receita) -> str:
        """1.0 → 1.1 → 1.2. Texto que não é número vira sufixo, sem estourar."""
        atual = (receita.ficha.versao or '1.0').strip()
        try:
            inteiro, decimal = atual.split('.', 1)
            return f'{int(inteiro)}.{int(decimal) + 1}'
        except (ValueError, AttributeError):
            return f'{atual}-nova'

    @staticmethod
    @transaction.atomic
    def ativar(receita: Receita) -> Receita:
        """
        Põe esta versão em uso e tira a anterior.

        SÓ UMA ATIVA POR PRODUTO. Duas ativas fariam a ordem de produção
        escolher — e "escolher" aqui significa produzir por uma fórmula que
        ninguém decidiu.
        """
        pendencias = receita.pendencias()
        if pendencias:
            raise DomainError(
                'A receita ainda não pode ser ativada — ' + ' '.join(pendencias)
            )

        (
            FichaTecnica.objects
            .filter(
                filial=receita.filial,
                produto_acabado=receita.produto,
                status=FichaTecnica.Status.ATIVA,
            )
            .exclude(pk=receita.ficha_id)
            .update(status=FichaTecnica.Status.INATIVA)
        )
        receita.ficha.status = FichaTecnica.Status.ATIVA
        receita.ficha.save(update_fields=['status'])
        return receita

    # ── Itens ────────────────────────────────────────────────────────────

    @staticmethod
    def itens(receita: Receita) -> dict:
        """
        Os itens separados em ingredientes e embalagem, com participação.

        A SEPARAÇÃO SAI DA FICHA DO PRODUTO (`FichaProduto.classe`), e não de
        uma marcação repetida no item da receita: o pote já foi cadastrado
        como embalagem uma vez, e perguntar de novo a cada receita é como as
        duas respostas passam a divergir.

        A PARTICIPAÇÃO É SOBRE OS INGREDIENTES, não sobre tudo. Somar o pote
        na base faria "60% de fruta" virar 58% porque a embalagem entrou na
        conta — e é o percentual de fruta que o rótulo declara.
        """
        ingredientes, embalagens = [], []
        for item in receita.ficha.itens.select_related(
            'materia_prima', 'materia_prima__unidade_medida',
            'materia_prima__ficha_polpa',
        ):
            ficha = getattr(item.materia_prima, 'ficha_polpa', None)
            destino = (
                embalagens
                if ficha and ficha.classe == FichaProduto.Classe.EMBALAGEM
                else ingredientes
            )
            destino.append(item)

        base = sum((i.quantidade for i in ingredientes), ZERO)
        linhas = []
        for item in ingredientes:
            participacao = (
                (item.quantidade / base * CEM).quantize(Decimal('0.01'))
                if base else ZERO
            )
            linhas.append({'item': item, 'participacao': participacao})

        return {
            'ingredientes': linhas,
            'embalagens': embalagens,
            'base': base,
        }

    # ── Custos ───────────────────────────────────────────────────────────

    @classmethod
    def custos(cls, receita: Receita) -> dict:
        """
        O custo da batida inteira e as divisões que a fábrica pergunta.

        `None` onde a conta não é possível (sem peso, sem quantidade): zero
        seria lido como "de graça", e é assim que um produto entra na tabela
        de preço abaixo do custo.
        """
        separados = cls.itens(receita)
        ficha = receita.ficha

        custo_mp = sum(
            (cls._custo_do_item(l['item']) for l in separados['ingredientes']), ZERO,
        )
        custo_emb = sum(
            (cls._custo_do_item(item) for item in separados['embalagens']), ZERO,
        )
        processo = (ficha.custo_mao_obra_padrao or ZERO) + (ficha.custo_indireto_padrao or ZERO)
        total = custo_mp + custo_emb + processo

        produzido = ficha.quantidade_produzida or ZERO
        produto = receita.produto
        peso_unitario = produto.peso_liquido or ZERO
        por_caixa = produto.quantidade_por_embalagem or ZERO

        custo_unidade = (total / produzido).quantize(CENTAVO) if produzido else None
        # O PESO TOTAL sai da quantidade vezes o peso de cada unidade: o
        # produto é vendido em unidade, mas a fruta é comprada em quilo, e é
        # em quilo que a indústria compara custo entre receitas.
        peso_total = produzido * peso_unitario if produzido and peso_unitario else ZERO

        return {
            'materia_prima': custo_mp.quantize(Decimal('0.01')),
            'embalagem': custo_emb.quantize(Decimal('0.01')),
            'processo': processo.quantize(Decimal('0.01')),
            'total': total.quantize(Decimal('0.01')),
            'por_unidade': custo_unidade,
            'por_kg': (
                (total / peso_total).quantize(CENTAVO) if peso_total else None
            ),
            'por_caixa': (
                (custo_unidade * por_caixa).quantize(Decimal('0.01'))
                if custo_unidade is not None and por_caixa else None
            ),
            'peso_total': peso_total,
            'unidades': produzido,
        }

    @staticmethod
    def _custo_do_item(item: ItemFichaTecnica) -> Decimal:
        """
        Quantidade COM a perda prevista, pelo custo médio do produto.

        O custo médio e não o último preço: uma compra cara isolada
        distorceria a receita inteira até a próxima compra.
        """
        produto = item.materia_prima
        custo = produto.preco_custo_medio or produto.preco_custo or ZERO
        return item.quantidade_com_perda() * custo

    # ── Rendimento ───────────────────────────────────────────────────────

    @staticmethod
    def rendimento_real(receita: Receita) -> dict:
        """
        O que a fábrica realmente rendeu, pelas ordens encerradas.

        `None` sem nenhuma ordem: zero seria lido como rendimento péssimo, e
        receita nova nasceria parecendo problema.
        """
        ordens = list(
            OrdemProducao.objects
            .filter(ficha_tecnica=receita.ficha, status=OrdemProducao.Status.ENCERRADA)
            .only('peso_entrada_mp', 'peso_saida_produzido', 'rendimento')
        )
        if not ordens:
            return {'ordens': 0, 'percentual': None, 'perda': None}

        entrada = sum((o.peso_entrada_mp or ZERO for o in ordens), ZERO)
        saida = sum((o.peso_saida_produzido or ZERO for o in ordens), ZERO)

        if entrada > ZERO:
            # PESO ENTRA, PESO SAI é a conta certa aqui. `OrdemProducao.
            # rendimento` compara produzido com PLANEJADO, que responde
            # "cumpri a meta?" -- outra pergunta. Rendimento de polpa é
            # quanto da fruta virou produto.
            percentual = (saida / entrada * CEM).quantize(Decimal('0.01'))
        else:
            media = sum((o.rendimento or ZERO for o in ordens), ZERO) / len(ordens)
            percentual = media.quantize(Decimal('0.01'))

        return {
            'ordens': len(ordens),
            'percentual': percentual,
            'perda': (CEM - percentual).quantize(Decimal('0.01')),
            'entrada': entrada,
            'saida': saida,
        }

    @classmethod
    def desvio_de_rendimento(cls, receita: Receita):
        """
        Real menos esperado, em pontos percentuais.

        É O NÚMERO QUE PAGA A CONTA: dois pontos abaixo do esperado numa
        fábrica que processa 40 toneladas por dia são 800 kg de polpa que
        alguém pagou e não vendeu.
        """
        real = cls.rendimento_real(receita)
        if real['percentual'] is None or not receita.rendimento_esperado:
            return None
        return (real['percentual'] - receita.rendimento_esperado).quantize(Decimal('0.01'))
