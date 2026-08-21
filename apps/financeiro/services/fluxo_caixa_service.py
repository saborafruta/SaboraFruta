"""Fluxo de caixa -- realizado (o que efetivamente entrou/saiu) e projetado
(o que ainda vai vencer). Reaproveita as duas fontes de verdade que ja
existem: MovimentacaoCaixa (dinheiro que passa pelo caixa do PDV) e as
baixas de ContaReceber/ContaPagar (tudo que se resolve fora do caixa --
boletos, transferencias, fornecedores). Nao ha overlap entre as duas: uma
venda a vista no PDV vira MovimentacaoCaixa e nao gera ContaReceber; so
vendas a prazo (crediario) geram ContaReceber, e essas sao liquidadas
depois, fora do caixa.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncDate

from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber
from apps.pdv.models.sessao import MovimentacaoCaixa

TIPOS_ENTRADA_CAIXA = ['venda', 'suprimento', 'tef_entrada']
TIPOS_SAIDA_CAIXA = ['sangria', 'cancelamento_venda', 'devolucao', 'troco', 'tef_saida']

STATUS_RECEBER_PREVISTO = [
    StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO, StatusContaReceber.NEGOCIADO,
]
STATUS_PAGAR_PREVISTO = [
    StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO, StatusContaPagar.AGENDADO,
]


class FluxoCaixaService:

    @classmethod
    def apurar(cls, filial, data_inicio: date, data_fim: date):
        entradas_caixa = MovimentacaoCaixa.objects.filter(
            filial=filial, tipo__in=TIPOS_ENTRADA_CAIXA,
            data_movimentacao__date__gte=data_inicio, data_movimentacao__date__lte=data_fim,
        ).aggregate(total=Sum('valor'))['total'] or Decimal('0')

        saidas_caixa = MovimentacaoCaixa.objects.filter(
            filial=filial, tipo__in=TIPOS_SAIDA_CAIXA,
            data_movimentacao__date__gte=data_inicio, data_movimentacao__date__lte=data_fim,
        ).aggregate(total=Sum('valor'))['total'] or Decimal('0')

        recebimentos = ContaReceber.objects.filter(
            filial=filial, status=StatusContaReceber.PAGO,
            data_pagamento__gte=data_inicio, data_pagamento__lte=data_fim,
        )
        entradas_receber = sum((item.valor_entrada_liquida for item in recebimentos), Decimal('0'))

        saidas_pagar = ContaPagar.objects.filter(
            filial=filial, status=StatusContaPagar.PAGO,
            data_pagamento__gte=data_inicio, data_pagamento__lte=data_fim,
        ).aggregate(total=Sum('valor_pago'))['total'] or Decimal('0')

        entradas_realizadas = entradas_caixa + entradas_receber
        saidas_realizadas = saidas_caixa + saidas_pagar
        saldo_periodo = entradas_realizadas - saidas_realizadas

        a_receber_previsto = ContaReceber.objects.filter(
            filial=filial, status__in=STATUS_RECEBER_PREVISTO,
            data_vencimento__gte=data_inicio, data_vencimento__lte=data_fim,
        ).aggregate(total=Sum('valor_saldo'))['total'] or Decimal('0')

        a_pagar_previsto = ContaPagar.objects.filter(
            filial=filial, status__in=STATUS_PAGAR_PREVISTO,
            data_vencimento__gte=data_inicio, data_vencimento__lte=data_fim,
        ).aggregate(total=Sum('valor_saldo'))['total'] or Decimal('0')

        return {
            'entradas_realizadas': entradas_realizadas,
            'saidas_realizadas': saidas_realizadas,
            'saldo_periodo': saldo_periodo,
            'a_receber_previsto': a_receber_previsto,
            'a_pagar_previsto': a_pagar_previsto,
            'saldo_projetado': saldo_periodo + a_receber_previsto - a_pagar_previsto,
            'serie_diaria': cls._serie_diaria(filial, data_inicio, data_fim),
        }

    @staticmethod
    def _serie_diaria(filial, data_inicio: date, data_fim: date):
        entradas_por_dia = dict(
            MovimentacaoCaixa.objects.filter(
                filial=filial, tipo__in=TIPOS_ENTRADA_CAIXA,
                data_movimentacao__date__gte=data_inicio, data_movimentacao__date__lte=data_fim,
            ).annotate(dia=TruncDate('data_movimentacao')).values('dia')
            .annotate(total=Sum('valor')).values_list('dia', 'total')
        )
        saidas_por_dia = dict(
            MovimentacaoCaixa.objects.filter(
                filial=filial, tipo__in=TIPOS_SAIDA_CAIXA,
                data_movimentacao__date__gte=data_inicio, data_movimentacao__date__lte=data_fim,
            ).annotate(dia=TruncDate('data_movimentacao')).values('dia')
            .annotate(total=Sum('valor')).values_list('dia', 'total')
        )
        receber_por_dia = defaultdict(lambda: Decimal('0'))
        for item in ContaReceber.objects.filter(
            filial=filial, status=StatusContaReceber.PAGO,
            data_pagamento__gte=data_inicio, data_pagamento__lte=data_fim,
        ):
            receber_por_dia[item.data_pagamento] += item.valor_entrada_liquida
        pagar_por_dia = dict(
            ContaPagar.objects.filter(
                filial=filial, status=StatusContaPagar.PAGO,
                data_pagamento__gte=data_inicio, data_pagamento__lte=data_fim,
            ).values('data_pagamento').annotate(total=Sum('valor_pago'))
            .values_list('data_pagamento', 'total')
        )

        serie = []
        acumulado = Decimal('0')
        cursor = data_inicio
        while cursor <= data_fim:
            entradas = (entradas_por_dia.get(cursor) or Decimal('0')) + (receber_por_dia.get(cursor) or Decimal('0'))
            saidas = (saidas_por_dia.get(cursor) or Decimal('0')) + (pagar_por_dia.get(cursor) or Decimal('0'))
            acumulado += entradas - saidas
            serie.append({
                'data': cursor,
                'entradas': entradas,
                'saidas': saidas,
                'saldo_dia': entradas - saidas,
                'saldo_acumulado': acumulado,
            })
            cursor += timedelta(days=1)
        return serie
