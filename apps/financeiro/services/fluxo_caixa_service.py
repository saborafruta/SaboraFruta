"""Fluxo de caixa gerencial: previsto x realizado, diário e mensal."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models import (
    ContaPagar, ContaReceber, PagamentoContaPagar, PagamentoContaReceber,
)
from apps.pdv.models.sessao import MovimentacaoCaixa

ZERO = Decimal('0')
TIPOS_ENTRADA_CAIXA = ('venda', 'suprimento', 'tef_entrada')
TIPOS_SAIDA_CAIXA = ('sangria', 'cancelamento_venda', 'devolucao', 'troco', 'tef_saida')
RECEBER_MORTOS = (StatusContaReceber.CANCELADO, StatusContaReceber.DEVOLVIDO)
PAGAR_MORTOS = (StatusContaPagar.CANCELADO,)


def _mes_seguinte(valor: date) -> date:
    if valor.month == 12:
        return valor.replace(year=valor.year + 1, month=1, day=1)
    return valor.replace(month=valor.month + 1, day=1)


class FluxoCaixaService:
    @classmethod
    def apurar(cls, filial, data_inicio: date, data_fim: date):
        if data_fim < data_inicio:
            data_inicio, data_fim = data_fim, data_inicio

        diario = cls._mapa_diario(filial, data_inicio, data_fim)
        serie_diaria = []
        acumulado_realizado = ZERO
        acumulado_previsto = ZERO
        cursor = data_inicio
        while cursor <= data_fim:
            linha = diario[cursor]
            linha['saldo_previsto'] = linha['entradas_previstas'] - linha['saidas_previstas']
            linha['saldo_realizado'] = linha['entradas_realizadas'] - linha['saidas_realizadas']
            linha['variacao_entradas'] = linha['entradas_realizadas'] - linha['entradas_previstas']
            linha['variacao_saidas'] = linha['saidas_realizadas'] - linha['saidas_previstas']
            linha['variacao'] = linha['saldo_realizado'] - linha['saldo_previsto']
            acumulado_previsto += linha['saldo_previsto']
            acumulado_realizado += linha['saldo_realizado']
            linha['saldo_previsto_acumulado'] = acumulado_previsto
            linha['saldo_realizado_acumulado'] = acumulado_realizado
            linha['data'] = cursor
            serie_diaria.append(linha)
            cursor += timedelta(days=1)

        serie_mensal = cls._agrupar_meses(serie_diaria, data_inicio, data_fim)
        totais = {
            chave: sum((linha[chave] for linha in serie_diaria), ZERO)
            for chave in ('entradas_previstas', 'entradas_realizadas', 'saidas_previstas', 'saidas_realizadas')
        }
        totais.update({
            'saldo_previsto': totais['entradas_previstas'] - totais['saidas_previstas'],
            'saldo_realizado': totais['entradas_realizadas'] - totais['saidas_realizadas'],
        })
        totais['variacao'] = totais['saldo_realizado'] - totais['saldo_previsto']
        totais.update({
            'a_receber_previsto': totais['entradas_previstas'],
            'a_pagar_previsto': totais['saidas_previstas'],
            'saldo_periodo': totais['saldo_realizado'],
            'saldo_projetado': totais['saldo_previsto'],
            'serie_diaria': serie_diaria,
            'serie_mensal': serie_mensal,
        })
        return totais

    @staticmethod
    def _mapa_diario(filial, inicio, fim):
        mapa = defaultdict(lambda: {
            'entradas_previstas': ZERO, 'entradas_realizadas': ZERO,
            'saidas_previstas': ZERO, 'saidas_realizadas': ZERO,
        })
        # O previsto original permanece visível mesmo depois da baixa.
        for vencimento, valor in (
            ContaReceber.objects.for_filial(filial).exclude(status__in=RECEBER_MORTOS)
            .filter(data_vencimento__range=(inicio, fim)).values_list('data_vencimento', 'valor_final')
        ):
            mapa[vencimento]['entradas_previstas'] += valor or ZERO
        for vencimento, valor in (
            ContaPagar.objects.for_filial(filial).exclude(status__in=PAGAR_MORTOS)
            .filter(data_vencimento__range=(inicio, fim)).values_list('data_vencimento', 'valor_final')
        ):
            mapa[vencimento]['saidas_previstas'] += valor or ZERO

        # Baixas individuais preservam pagamentos parciais no mês correto.
        for pagamento in (
            PagamentoContaReceber.objects.filter(
                filial=filial, data_pagamento__range=(inicio, fim),
                conta_receber__excluido_em__isnull=True,
            ).exclude(conta_receber__status__in=RECEBER_MORTOS)
        ):
            mapa[pagamento.data_pagamento]['entradas_realizadas'] += pagamento.valor_liquido or pagamento.valor_pago or ZERO
        for pagamento in (
            PagamentoContaPagar.objects.filter(
                filial=filial, data_pagamento__range=(inicio, fim),
                conta_pagar__excluido_em__isnull=True,
            ).exclude(conta_pagar__status__in=PAGAR_MORTOS)
        ):
            mapa[pagamento.data_pagamento]['saidas_realizadas'] += pagamento.valor_pago or ZERO

        for movimento in MovimentacaoCaixa.objects.filter(
            filial=filial, data_movimentacao__date__range=(inicio, fim),
            tipo__in=TIPOS_ENTRADA_CAIXA + TIPOS_SAIDA_CAIXA,
        ).only('tipo', 'valor', 'data_movimentacao'):
            dia = movimento.data_movimentacao.date()
            chave = 'entradas_realizadas' if movimento.tipo in TIPOS_ENTRADA_CAIXA else 'saidas_realizadas'
            mapa[dia][chave] += movimento.valor or ZERO
        return mapa

    @staticmethod
    def _agrupar_meses(serie_diaria, inicio, fim):
        meses = {}
        cursor = inicio.replace(day=1)
        limite = fim.replace(day=1)
        while cursor <= limite:
            meses[(cursor.year, cursor.month)] = {
                'mes': cursor, 'entradas_previstas': ZERO, 'entradas_realizadas': ZERO,
                'saidas_previstas': ZERO, 'saidas_realizadas': ZERO,
            }
            cursor = _mes_seguinte(cursor)
        for dia in serie_diaria:
            linha = meses[(dia['data'].year, dia['data'].month)]
            for chave in ('entradas_previstas', 'entradas_realizadas', 'saidas_previstas', 'saidas_realizadas'):
                linha[chave] += dia[chave]
        for linha in meses.values():
            linha['saldo_previsto'] = linha['entradas_previstas'] - linha['saidas_previstas']
            linha['saldo_realizado'] = linha['entradas_realizadas'] - linha['saidas_realizadas']
            linha['variacao'] = linha['saldo_realizado'] - linha['saldo_previsto']
        return list(meses.values())
