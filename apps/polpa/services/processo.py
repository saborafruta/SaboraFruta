"""
As regras do processo: quais etapas uma ordem tem, e o que se aponta nelas.

AS ETAPAS NASCEM COM A ORDEM, e não quando alguém aponta a primeira. Se
nascessem sob demanda, uma OP aberta mostraria só o que já foi tocado — e
"não iniciada" ficaria indistinguível de "não existe", que é exatamente a
informação que quem acompanha a produção precisa.

QUAIS ETAPAS uma ordem tem sai da RECEITA quando ela declara (a ficha
técnica da seção 2 lista as etapas do produto, e cada uma pode apontar para
uma etapa canônica). Sem isso, entra o FLUXO DO TIPO do produto: polpa passa
por despolpamento e refino; açaí, por processamento, mistura e resfriamento.
As etapas "quando aplicável" -- descascamento, corte, formulação e
pasteurização -- ficam de fora do padrão e entram quando a receita as
declara: linha vazia por padrão é o que faz a pessoa parar de olhar a lista.

APONTAR NÃO REESCREVE A RECEITA. O que se grava aqui é o que aconteceu
nesta batida; a fórmula continua onde estava. Um campo só para as duas
coisas faria a receita ser reescrita a cada produção.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.polpa.models import ApontamentoEtapa, OrdemPolpa
from apps.polpa.models.processo import POSICAO, Etapa, fluxo_do_produto

ZERO = Decimal('0')
SIT = ApontamentoEtapa.Situacao


class ProcessoService:

    # ── Montagem ─────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def preparar(op: OrdemPolpa) -> list[ApontamentoEtapa]:
        """
        Cria as etapas desta ordem, se ainda não existirem.

        IDEMPOTENTE de propósito: é chamada na criação e de novo na
        liberação, e uma segunda chamada não pode duplicar a lista nem
        apagar o que já foi apontado.
        """
        existentes = set(
            op.etapas_processo.values_list('etapa', flat=True)
        )
        # O CAMINHO SAI DO PRODUTO quando a receita não declara: polpa e
        # açaí não passam pelas mesmas etapas, e uma lista única faria a
        # tela do açaí mostrar despolpamento e refino, que ele não tem.
        escolhidas = (
            ProcessoService._etapas_da_receita(op)
            or list(fluxo_do_produto(op.produto))
        )

        novas = [
            ApontamentoEtapa(
                filial=op.filial, ordem=op, etapa=etapa,
                sequencia=POSICAO.get(etapa, 99),
            )
            for etapa in escolhidas if etapa not in existentes
        ]
        if novas:
            ApontamentoEtapa.objects.bulk_create(novas)
        return list(
            op.etapas_processo.select_related('operador', 'equipamento', 'lote')
        )

    @staticmethod
    def _etapas_da_receita(op: OrdemPolpa) -> list[str]:
        """
        As etapas canônicas que a receita declara, na ordem do processo.

        A receita pode ter etapas livres (sem vínculo com as dezoito) --
        elas continuam valendo como INSTRUÇÃO na tela da ordem, mas não
        viram apontamento: apontar uma etapa que não existe no vocabulário
        comum é o que faz o rendimento por etapa deixar de somar.
        """
        declaradas = [
            e.etapa for e in op.receita.etapas.all() if e.etapa
        ]
        return sorted(set(declaradas), key=lambda e: POSICAO.get(e, 99))

    # ── Apontamento ──────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def apontar(etapa: ApontamentoEtapa, dados: dict, usuario=None) -> ApontamentoEtapa:
        """
        Grava o que aconteceu na etapa.

        O OPERADOR É QUEM EXECUTOU, e não quem digitou -- a tela oferece a
        escolha, e o padrão é quem está logado porque na maior parte dos
        casos são a mesma pessoa. Sem esse campo, "quem fez" vira memória.
        """
        if etapa.ordem.encerrada:
            raise DomainError(
                'Esta ordem já foi encerrada — o processo dela não muda mais.'
            )

        for campo in (
            'quantidade_entrada', 'quantidade_saida',
            'volume_entrada', 'volume_saida', 'temperatura',
            'motivo_perda', 'observacao', 'equipamento', 'lote',
        ):
            if campo in dados:
                setattr(etapa, campo, dados[campo])

        etapa.operador = dados.get('operador') or etapa.operador or usuario

        agora = timezone.now()
        situacao = dados.get('situacao') or SIT.CONCLUIDA
        if situacao == SIT.PULADA:
            etapa.situacao = SIT.PULADA
            etapa.concluida_em = agora
        elif situacao == SIT.EM_ANDAMENTO:
            etapa.situacao = SIT.EM_ANDAMENTO
            etapa.iniciada_em = etapa.iniciada_em or agora
            etapa.concluida_em = None
        else:
            etapa.situacao = SIT.CONCLUIDA
            etapa.iniciada_em = etapa.iniciada_em or agora
            etapa.concluida_em = agora

        etapa.save()
        return etapa

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def resumo(op: OrdemPolpa) -> dict:
        """
        Onde a fruta se perde, etapa a etapa.

        O RENDIMENTO ACUMULADO é o que importa no fim: a primeira etapa
        recebe 1.000 kg de fruta e a última entrega 600 kg de polpa. As
        perdas se compõem pelo caminho, e um total isolado não diz em qual
        máquina mexer.
        """
        etapas = list(
            op.etapas_processo.select_related('operador', 'equipamento')
        )
        apontadas = [e for e in etapas if e.situacao == SIT.CONCLUIDA]

        entrada = next(
            (e.quantidade_entrada for e in etapas
             if e.quantidade_entrada is not None), None,
        )
        saida = next(
            (e.quantidade_saida for e in reversed(etapas)
             if e.quantidade_saida is not None), None,
        )

        rendimento = None
        if entrada and saida is not None and entrada > ZERO:
            rendimento = (saida / entrada * 100).quantize(Decimal('0.01'))

        perdas = [
            {'etapa': e, 'perda': e.perda, 'percentual': e.perda_percentual}
            for e in etapas if e.perda
        ]
        perdas.sort(key=lambda p: p['perda'], reverse=True)

        return {
            'etapas': etapas,
            'total': len(etapas),
            'concluidas': len(apontadas),
            'pendentes': sum(1 for e in etapas if e.situacao == SIT.PENDENTE),
            'entrada': entrada,
            'saida': saida,
            'rendimento': rendimento,
            'perda_total': (
                (entrada - saida) if entrada is not None and saida is not None else None
            ),
            'maiores_perdas': perdas[:3],
            # O OVERRUN DA BATIDA, quando alguém mediu. É o número que
            # decide quantos potes saem de 100 litros de base -- ou seja, a
            # margem do sorvete.
            'overrun': next(
                (e.overrun for e in etapas if e.overrun is not None), None,
            ),
            'proxima': next(
                (e for e in etapas if e.situacao == SIT.PENDENTE), None,
            ),
        }

    @staticmethod
    def pendencias(op: OrdemPolpa) -> list[str]:
        """
        O que o processo ainda não registrou.

        Não trava nada: é lista para quem encerra decidir com o que está na
        mão. A fábrica trabalha com o apontamento atrasado o tempo todo —
        travar o encerramento por etapa não apontada faria a pessoa apontar
        qualquer coisa para conseguir fechar.
        """
        faltando = []
        for etapa in op.etapas_processo.all():
            if etapa.situacao != SIT.PENDENTE:
                continue
            faltando.append(f'{etapa.get_etapa_display()} não foi apontada.')
        return faltando

    @staticmethod
    def consumo(op: OrdemPolpa) -> dict:
        """
        O que a receita mandava consumir, contra o que saiu do estoque.

        PREVISTO E REALIZADO LADO A LADO. O previsto sai da receita
        (quantidade × fator da ordem); o realizado sai das MOVIMENTAÇÕES da
        OP, que é o que de fato baixou. Mostrar só o previsto seria mostrar
        a intenção; só o realizado esconderia que se gastou 12% a mais de
        açúcar do que a fórmula manda -- e é essa diferença que explica o
        custo do lote ter estourado.

        Enquanto a ordem não é encerrada não há movimentação, e o realizado
        vem vazio: `None` e não zero, porque "ainda não consumiu" é
        diferente de "consumiu nada".
        """
        from apps.estoque.models import MovimentacaoEstoque

        ficha = op.ordem.ficha_tecnica
        base = ficha.quantidade_produzida or ZERO
        fator = (op.quantidade_planejada / base) if base else ZERO

        movidos: dict = {}
        for mov in MovimentacaoEstoque.objects.filter(
            documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
            documento_id=op.ordem_id,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA,
        ):
            movidos[mov.produto_id] = movidos.get(mov.produto_id, ZERO) + (
                mov.quantidade or ZERO
            )

        linhas, custo_previsto, custo_real = [], ZERO, ZERO
        for item in ficha.itens.select_related(
            'materia_prima', 'materia_prima__unidade_medida',
        ):
            produto = item.materia_prima
            previsto = (item.quantidade_com_perda() * fator).quantize(Decimal('0.001'))
            realizado = movidos.get(produto.pk)
            custo = produto.preco_custo_medio or produto.preco_custo or ZERO

            custo_previsto += previsto * custo
            if realizado is not None:
                custo_real += realizado * custo

            linhas.append({
                'produto': produto,
                'unidade': getattr(produto.unidade_medida, 'sigla', ''),
                'previsto': previsto,
                'realizado': realizado,
                'diferenca': (
                    (realizado - previsto) if realizado is not None else None
                ),
                'custo_previsto': (previsto * custo).quantize(Decimal('0.01')),
                'custo_real': (
                    (realizado * custo).quantize(Decimal('0.01'))
                    if realizado is not None else None
                ),
            })

        return {
            'linhas': linhas,
            'custo_previsto': custo_previsto.quantize(Decimal('0.01')),
            'custo_real': custo_real.quantize(Decimal('0.01')) if movidos else None,
            'consumiu': bool(movidos),
        }

    @staticmethod
    def fila(filial, filtros: dict | None = None):
        """
        As etapas que estão esperando alguém — a fila do chão de fábrica.

        Ordenada por ordem e sequência: quem está na linha pega a próxima da
        SUA ordem, não a mais antiga do sistema.
        """
        filtros = filtros or {}
        qs = (
            ApontamentoEtapa.objects.for_filial(filial)
            .filter(ordem__situacao__in=OrdemPolpa.ABERTAS)
            .select_related(
                'ordem', 'ordem__ordem', 'ordem__ordem__produto_acabado',
                'operador', 'equipamento',
            )
        )
        if filtros.get('etapa'):
            qs = qs.filter(etapa=filtros['etapa'])
        if filtros.get('situacao'):
            qs = qs.filter(situacao=filtros['situacao'])
        elif not filtros.get('todas'):
            qs = qs.exclude(situacao=SIT.CONCLUIDA)
        return qs.order_by('ordem__ordem__numero', 'sequencia')

    @staticmethod
    def etapas_disponiveis() -> list[tuple[str, str]]:
        return [(e.value, e.label) for e in Etapa]
