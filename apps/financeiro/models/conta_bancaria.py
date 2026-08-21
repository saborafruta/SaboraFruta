"""Contas bancárias e plano de contas."""
from django.db import models
from apps.core.models import Empresa, Filial
from apps.core.models.base import TimestampedModel, ActiveModel
from apps.core.models.base import FilialManager as FilialAwareManager


class ContaBancaria(TimestampedModel, ActiveModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="contas_bancarias")
    banco_codigo = models.CharField(max_length=5)
    banco_nome = models.CharField(max_length=80, blank=True)
    agencia = models.CharField(max_length=6, blank=True)
    agencia_digito = models.CharField(max_length=1, blank=True)
    conta = models.CharField(max_length=12, blank=True)
    conta_digito = models.CharField(max_length=1, blank=True)
    tipo_conta = models.CharField(max_length=20, blank=True)
    descricao = models.CharField(max_length=80, blank=True)

    saldo_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo_atual = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    chave_pix = models.CharField(max_length=100, blank=True)
    tipo_chave_pix = models.CharField(max_length=20, blank=True)
    client_id_api = models.CharField(max_length=200, blank=True)
    client_secret_api = models.CharField(max_length=200, blank=True)
    certificado_mtls_path = models.CharField(max_length=500, blank=True)
    ambiente_api = models.CharField(max_length=20, default="sandbox")
    carteira_cobranca = models.CharField(max_length=4, blank=True)
    convenio = models.CharField(max_length=10, blank=True)
    nosso_numero_atual = models.BigIntegerField(default=1)

    objects = FilialAwareManager()

    class Meta:
        db_table = "contas_bancarias"
        verbose_name = "Conta bancária"
        verbose_name_plural = "Contas bancárias"

    def __str__(self):
        return f"{self.banco_nome} – Ag. {self.agencia} CC. {self.conta}"


class PlanoContas(ActiveModel):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="plano_contas", null=True, blank=True)
    conta_pai = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="filhas")
    conta_contabil = models.ForeignKey(
        "financeiro.PlanoContabil",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="categorias_financeiras",
    )
    codigo = models.CharField(max_length=20)
    descricao = models.CharField(max_length=100)
    tipo = models.CharField(
        max_length=1, choices=[("R", "Receita"), ("D", "Despesa")],
    )
    nivel = models.PositiveSmallIntegerField(default=1)
    aceita_lancamento = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "plano_contas"
        verbose_name = "Categoria financeira"
        verbose_name_plural = "Categorias financeiras"
        ordering = ["codigo"]

    def __str__(self):
        if self.descricao:
            return self.descricao
        return f"{self.codigo} – {self.descricao}"

    @property
    def caminho_descricao(self):
        partes = []
        atual = self
        while atual and len(partes) < 3:
            partes.append(atual.descricao)
            atual = atual.conta_pai
        return " > ".join(reversed(partes))
