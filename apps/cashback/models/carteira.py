"""Carteira virtual de cashback do cliente."""
from decimal import Decimal

from django.db import models

from apps.core.models.base import TimestampedModel


class CarteiraCashback(TimestampedModel):
    """
    Uma carteira por cliente por empresa — vale em qualquer filial da
    mesma empresa, pois o cashback segue o CPF/CNPJ do cliente, não a
    loja onde foi gerado.

    Os saldos abaixo são colunas de cache, atualizadas dentro da mesma
    transação de cada MovimentoCashback. A fonte da verdade é sempre o
    ledger (MovimentoCashback) — em qualquer decisão crítica (débito,
    expiração), o saldo é recalculado por agregação sob select_for_update,
    nunca lido apenas destes campos.
    """

    empresa = models.ForeignKey(
        "core.Empresa", on_delete=models.CASCADE, related_name="carteiras_cashback",
    )
    cliente = models.ForeignKey(
        "cadastros.Cliente", on_delete=models.PROTECT, related_name="carteiras_cashback",
    )

    saldo_disponivel = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    saldo_pendente = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    saldo_expirado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    saldo_utilizado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    saldo_cancelado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    saldo_total_gerado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "cashback_carteira"
        verbose_name = "Carteira de Cashback"
        verbose_name_plural = "Carteiras de Cashback"
        constraints = [
            models.UniqueConstraint(fields=["empresa", "cliente"], name="uniq_carteira_cashback_empresa_cliente"),
        ]
        indexes = [
            models.Index(fields=["empresa", "cliente"]),
        ]

    def __str__(self):
        return f"Carteira de {self.cliente} — R$ {self.saldo_disponivel}"
