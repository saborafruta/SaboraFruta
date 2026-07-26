"""Bloco 12 — Formas e condições de pagamento."""
from django.db import models
from apps.core.models import Empresa, Filial
from apps.core.models.base import ActiveModel
from ..constants.enums import TipoFormaPagamento


class FormaPagamento(ActiveModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="formas_pagamento")
    filial = models.ForeignKey(
        Filial,
        on_delete=models.CASCADE,
        related_name="formas_pagamento",
        null=True,
        blank=True,
    )
    descricao = models.CharField(max_length=60)
    tipo = models.CharField(max_length=30, choices=TipoFormaPagamento.choices)
    codigo_sefaz = models.CharField(max_length=2, blank=True)
    requer_tef = models.BooleanField(default=False)
    gera_parcelas = models.BooleanField(default=False)
    movimenta_caixa = models.BooleanField(
        default=True,
        help_text=(
            "Desmarque para formas como Doação/Permuta: a venda continua dando "
            "baixa no estoque normalmente, mas o valor não entra no total do "
            "caixa nem no financeiro."
        ),
    )
    prazo_liquidacao_dias = models.PositiveSmallIntegerField(default=0)
    taxa_administrativa = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "formas_pagamento"
        verbose_name = "Forma de pagamento"
        verbose_name_plural = "Formas de pagamento"
        ordering = ["descricao"]
        indexes = [
            models.Index(fields=["filial", "ativo"], name="forma_pagto_filial_ativo_idx"),
        ]

    def __str__(self):
        return self.descricao


class TaxaParcelamento(models.Model):
    forma_pagamento = models.ForeignKey(
        FormaPagamento, on_delete=models.CASCADE, related_name="taxas_parcelamento"
    )
    parcelas = models.PositiveSmallIntegerField()
    taxa = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        db_table = "taxa_parcelamento"
        unique_together = [("forma_pagamento", "parcelas")]
        ordering = ["parcelas"]

    def __str__(self):
        return f"{self.forma_pagamento} — {self.parcelas}x — {self.taxa}%"


class CondicaoPagamento(ActiveModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="condicoes_pagamento")
    descricao = models.CharField(max_length=80)
    numero_parcelas = models.PositiveSmallIntegerField(default=1)
    intervalo_dias = models.PositiveSmallIntegerField(default=30)
    dias_primeira_parcela = models.PositiveSmallIntegerField(default=0)
    desconto_avista = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    acrescimo = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "condicoes_pagamento"
        verbose_name = "Condição de pagamento"
        verbose_name_plural = "Condições de pagamento"
        ordering = ["descricao"]

    def __str__(self):
        return self.descricao
