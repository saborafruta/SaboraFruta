"""Conciliacao bancaria: casar lancamentos do extrato (ExtratoBancario) com
contas a receber/pagar ja baixadas. O model ConciliacaoBancaria ja existia
no schema mas sem nenhum service/view usando -- este e' o primeiro.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models.extrato import ConciliacaoBancaria, ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber

JANELA_DIAS_SUGESTAO = 3
TOLERANCIA_VALOR = Decimal('0.01')


class LancamentoTipo:
    RECEBER = 'conta_receber'
    PAGAR = 'conta_pagar'


class ConciliacaoService:

    @staticmethod
    def sugestoes(extrato: ExtratoBancario, limite: int = 5):
        """Candidatos a conciliar com este lancamento: mesmo valor
        (tolerancia de centavos) e baixa dentro de +-N dias da data do
        lancamento no extrato. Credito (valor>0) sugere Conta a Receber
        paga; debito (valor<0) sugere Conta a Pagar paga."""
        inicio = extrato.data_lancamento - timedelta(days=JANELA_DIAS_SUGESTAO)
        fim = extrato.data_lancamento + timedelta(days=JANELA_DIAS_SUGESTAO)
        ja_conciliados_receber = set(
            ConciliacaoBancaria.objects.filter(lancamento_tipo=LancamentoTipo.RECEBER)
            .values_list('lancamento_id', flat=True)
        )
        ja_conciliados_pagar = set(
            ConciliacaoBancaria.objects.filter(lancamento_tipo=LancamentoTipo.PAGAR)
            .values_list('lancamento_id', flat=True)
        )

        if extrato.valor > 0:
            candidatos = ContaReceber.objects.filter(
                filial=extrato.filial, status=StatusContaReceber.PAGO,
                data_pagamento__gte=inicio, data_pagamento__lte=fim,
                valor_pago__gte=extrato.valor - TOLERANCIA_VALOR,
                valor_pago__lte=extrato.valor + TOLERANCIA_VALOR,
            ).exclude(pk__in=ja_conciliados_receber).select_related('cliente')[:limite]
            return [{'tipo': LancamentoTipo.RECEBER, 'obj': c, 'valor': c.valor_pago, 'data': c.data_pagamento,
                      'descricao': f'Recebimento de {c.cliente}'} for c in candidatos]

        if extrato.valor < 0:
            valor_abs = abs(extrato.valor)
            candidatos = ContaPagar.objects.filter(
                filial=extrato.filial, status=StatusContaPagar.PAGO,
                data_pagamento__gte=inicio, data_pagamento__lte=fim,
                valor_pago__gte=valor_abs - TOLERANCIA_VALOR,
                valor_pago__lte=valor_abs + TOLERANCIA_VALOR,
            ).exclude(pk__in=ja_conciliados_pagar).select_related('fornecedor')[:limite]
            return [{'tipo': LancamentoTipo.PAGAR, 'obj': c, 'valor': c.valor_pago, 'data': c.data_pagamento,
                      'descricao': f'Pagamento a {c.fornecedor}'} for c in candidatos]

        return []

    @staticmethod
    @transaction.atomic
    def conciliar(extrato: ExtratoBancario, lancamento_tipo: str, lancamento_id: int, usuario, observacao: str = ''):
        if extrato.status == 'conciliado':
            raise DomainError('Este lançamento já está conciliado.')

        if lancamento_tipo == LancamentoTipo.RECEBER:
            conta = ContaReceber.objects.filter(pk=lancamento_id, filial=extrato.filial).first()
            if not conta:
                raise DomainError('Conta a receber não encontrada.')
            valor_lancamento = conta.valor_pago
        elif lancamento_tipo == LancamentoTipo.PAGAR:
            conta = ContaPagar.objects.filter(pk=lancamento_id, filial=extrato.filial).first()
            if not conta:
                raise DomainError('Conta a pagar não encontrada.')
            valor_lancamento = conta.valor_pago
        else:
            raise DomainError('Tipo de lançamento inválido.')

        diferenca = abs(extrato.valor) - valor_lancamento

        conciliacao = ConciliacaoBancaria.objects.create(
            extrato=extrato,
            lancamento_tipo=lancamento_tipo,
            lancamento_id=lancamento_id,
            diferenca=diferenca,
            observacao=observacao,
            usuario=usuario,
        )
        extrato.status = 'conciliado'
        extrato.save(update_fields=['status'])
        return conciliacao

    @staticmethod
    @transaction.atomic
    def desconciliar(conciliacao: ConciliacaoBancaria):
        extrato = conciliacao.extrato
        conciliacao.delete()
        extrato.status = 'importado'
        extrato.save(update_fields=['status'])
