"""Serviços de negócio para Contas a Receber."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models.receber_pagar import ContaReceber


class ContaReceberService:

    @staticmethod
    @transaction.atomic
    def criar(
        filial,
        cliente,
        valor_original: Decimal,
        data_emissao: date,
        data_vencimento: date,
        parcela: int = 1,
        total_parcelas: int = 1,
        documento_numero: str = '',
        forma_pagamento=None,
        plano_contas=None,
        observacao: str = '',
        usuario=None,
    ) -> ContaReceber:
        """Cria um lançamento manual de conta a receber."""
        conta = ContaReceber(
            filial=filial,
            cliente=cliente,
            documento_numero=documento_numero,
            parcela=parcela,
            total_parcelas=total_parcelas,
            valor_original=valor_original,
            valor_juros=Decimal('0'),
            valor_multa=Decimal('0'),
            valor_desconto=Decimal('0'),
            valor_final=valor_original,
            valor_pago=Decimal('0'),
            valor_saldo=valor_original,
            data_emissao=data_emissao,
            data_vencimento=data_vencimento,
            forma_pagamento=forma_pagamento,
            plano_contas=plano_contas,
            observacao=observacao,
            status=StatusContaReceber.ABERTO,
            usuario=usuario,
        )
        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def registrar_baixa(
        conta: ContaReceber,
        data_pagamento: date,
        valor_pago: Decimal,
        forma_pagamento,
        usuario,
        conta_bancaria=None,
        valor_juros: Decimal = Decimal('0'),
        valor_multa: Decimal = Decimal('0'),
        valor_desconto: Decimal = Decimal('0'),
        observacao: str = '',
    ) -> ContaReceber:
        """Registra o recebimento (total ou parcial) de uma conta a receber."""
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Não é possível baixar uma conta cancelada.')
        if conta.status == StatusContaReceber.PAGO:
            raise DomainError('Esta conta já foi integralmente recebida.')

        # Acumula acréscimos/descontos desta baixa
        conta.valor_juros += valor_juros or Decimal('0')
        conta.valor_multa += valor_multa or Decimal('0')
        conta.valor_desconto += valor_desconto or Decimal('0')

        # Recalcula valor final
        conta.valor_final = (
            conta.valor_original
            + conta.valor_juros
            + conta.valor_multa
            - conta.valor_desconto
        )
        if conta.valor_final < Decimal('0'):
            conta.valor_final = Decimal('0')

        # Acumula valor pago e recalcula saldo
        conta.valor_pago += valor_pago
        conta.valor_saldo = conta.valor_final - conta.valor_pago

        if conta.valor_saldo <= Decimal('0'):
            conta.valor_saldo = Decimal('0')
            conta.status = StatusContaReceber.PAGO
        else:
            # Baixa parcial — mantém aberto
            conta.status = StatusContaReceber.ABERTO

        conta.data_pagamento = data_pagamento
        conta.forma_pagamento = forma_pagamento
        if conta_bancaria:
            conta.conta_bancaria = conta_bancaria
        conta.usuario_baixa = usuario

        if observacao:
            sufixo = f'[Baixa {data_pagamento:%d/%m/%Y}] {observacao}'
            conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo

        conta.save()
        return conta

    @staticmethod
    @transaction.atomic
    def cancelar(conta: ContaReceber, motivo: str, usuario) -> ContaReceber:
        """Cancela uma conta a receber ainda não paga."""
        if conta.status == StatusContaReceber.PAGO:
            raise DomainError('Não é possível cancelar uma conta já recebida.')
        if conta.status == StatusContaReceber.CANCELADO:
            raise DomainError('Esta conta já está cancelada.')

        conta.status = StatusContaReceber.CANCELADO
        sufixo = f'[Cancelado por {usuario} em {timezone.localdate():%d/%m/%Y}] {motivo}'
        conta.observacao = f'{conta.observacao}\n{sufixo}'.strip() if conta.observacao else sufixo
        conta.save()
        return conta

    @staticmethod
    def atualizar_status_vencidos(filial) -> int:
        """Marca como VENCIDO contas com data_vencimento < hoje e status ABERTO."""
        hoje = timezone.localdate()
        atualizado = (
            ContaReceber.objects
            .for_filial(filial)
            .filter(status=StatusContaReceber.ABERTO, data_vencimento__lt=hoje)
            .update(status=StatusContaReceber.VENCIDO)
        )
        return atualizado
