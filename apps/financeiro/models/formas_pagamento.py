"""Bloco 12 — Formas e condições de pagamento."""
from decimal import Decimal, ROUND_HALF_UP

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
    exibir_no_pdv = models.BooleanField(
        "Exibir no PDV",
        default=True,
        help_text="Desmarque para usar esta forma somente no Financeiro, sem exibi-la no PDV.",
    )
    movimenta_caixa = models.BooleanField(
        default=True,
        help_text=(
            "Desmarque para formas como Doação/Permuta: a venda continua dando "
            "baixa no estoque normalmente, mas o valor não entra no total do "
            "caixa nem no financeiro."
        ),
    )
    prazo_liquidacao_dias = models.PositiveSmallIntegerField(default=0)
    prazo_compensacao_dias_uteis = models.PositiveSmallIntegerField(
        default=0,
        help_text="Dias uteis entre a transacao e o credito na conta bancaria.",
    )
    taxa_administrativa = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    taxa_fixa = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Valor fixo descontado em cada transação recebida.",
    )
    tarifa_pagamento_fixa = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Tarifa cobrada pelo banco quando esta forma é usada para pagar.",
    )
    conta_bancaria_padrao = models.ForeignKey(
        "financeiro.ContaBancaria",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="formas_pagamento_padrao",
    )
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
        return self.descricao_com_conta

    @property
    def descricao_com_conta(self):
        if self.conta_bancaria_padrao_id:
            conta = self.conta_bancaria_padrao.descricao or self.conta_bancaria_padrao.banco_nome
            if conta:
                return f"{self.descricao} - {conta}"
        return self.descricao

    @staticmethod
    def normalizar_bandeira(bandeira=""):
        valor = (bandeira or "").strip().casefold().replace(" ", "")
        aliases = {
            "master": "mastercard", "mastercard": "mastercard",
            "visa": "visa", "elo": "elo",
            "amex": "amex", "americanexpress": "amex",
            "hiper": "hiper", "hipercard": "hiper",
        }
        return aliases.get(valor, valor[:20])

    def percentual_para_parcelas(self, parcelas=1, bandeira=""):
        """Retorna a taxa da parcela quando configurada, ou a taxa padrão."""
        parcelas = max(int(parcelas or 1), 1)
        bandeira = self.normalizar_bandeira(bandeira)
        taxas = self.taxas_parcelamento.filter(parcelas=parcelas)
        taxa_parcela = None
        if bandeira:
            taxa_parcela = taxas.filter(bandeira=bandeira).values_list("taxa", flat=True).first()
        if taxa_parcela is None:
            taxa_parcela = taxas.filter(bandeira="").values_list("taxa", flat=True).first()
        return Decimal(taxa_parcela if taxa_parcela is not None else self.taxa_administrativa or 0)

    @staticmethod
    def calcular_valores_taxa(valor_bruto, percentual=0, taxa_fixa=0):
        centavos = Decimal("0.01")
        bruto = max(Decimal(valor_bruto or 0), Decimal("0"))
        percentual = max(Decimal(percentual or 0), Decimal("0"))
        fixa = max(Decimal(taxa_fixa or 0), Decimal("0"))
        taxa = ((bruto * percentual / Decimal("100")) + fixa).quantize(centavos, rounding=ROUND_HALF_UP)
        taxa = min(taxa, bruto)
        return {
            "percentual": percentual,
            "fixa": fixa,
            "taxa": taxa,
            "liquido": (bruto - taxa).quantize(centavos, rounding=ROUND_HALF_UP),
        }

    def calcular_taxa_recebimento(self, valor_bruto, parcelas=1, bandeira=""):
        return self.calcular_valores_taxa(
            valor_bruto,
            self.percentual_para_parcelas(parcelas, bandeira),
            self.taxa_fixa,
        )


class TaxaParcelamento(models.Model):
    forma_pagamento = models.ForeignKey(
        FormaPagamento, on_delete=models.CASCADE, related_name="taxas_parcelamento"
    )
    parcelas = models.PositiveSmallIntegerField()
    bandeira = models.CharField(max_length=20, blank=True, default="")
    taxa = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        db_table = "taxa_parcelamento"
        unique_together = [("forma_pagamento", "parcelas", "bandeira")]
        ordering = ["parcelas", "bandeira"]

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
