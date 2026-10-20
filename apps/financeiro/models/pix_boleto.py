"""PIX, Boletos, Remessas, Retornos."""
from django.db import models
from apps.core.models import Filial, Usuario
from apps.core.models.base import TimestampedModel
from apps.core.models.base import FilialManager as FilialAwareManager
from .conta_bancaria import ContaBancaria
from .receber_pagar import ContaReceber
from ..constants.enums import StatusPIX


class PIXCobranca(TimestampedModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="pix_cobrancas")
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT)
    origem_tipo = models.CharField(max_length=30, blank=True)
    origem_id = models.BigIntegerField(null=True, blank=True)

    txid = models.CharField(max_length=35, unique=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    descricao = models.CharField(max_length=140, blank=True)
    qrcode_payload = models.TextField()
    qrcode_imagem_url = models.URLField(max_length=500, blank=True)

    status = models.CharField(
        max_length=20, choices=StatusPIX.choices, default=StatusPIX.PENDENTE,
    )
    e2e_id = models.CharField(max_length=35, blank=True)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    data_pagamento = models.DateTimeField(null=True, blank=True)
    pagador_cpf_cnpj = models.CharField(max_length=14, blank=True)
    pagador_nome = models.CharField(max_length=100, blank=True)
    data_expiracao = models.DateTimeField()
    response_criacao = models.JSONField(null=True, blank=True)
    response_pagamento = models.JSONField(null=True, blank=True)

    objects = FilialAwareManager()

    class Meta:
        db_table = "pix_cobrancas"
        verbose_name = "PIX cobrança"
        verbose_name_plural = "PIX cobranças"
        ordering = ["-created_at"]


class RemessaBancaria(models.Model):
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT)
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    numero_remessa = models.IntegerField()
    arquivo_nome = models.CharField(max_length=80, blank=True)
    arquivo_cnab = models.TextField(blank=True)
    quantidade_boletos = models.IntegerField(default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, default="gerado")
    layout = models.CharField(max_length=10, default="cnab240")
    arquivo_hash = models.CharField(max_length=64, blank=True, db_index=True)
    data_envio = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "remessas_bancarias"


class RetornoBancario(models.Model):
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT)
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    arquivo_nome = models.CharField(max_length=80, blank=True)
    arquivo_cnab = models.TextField(blank=True)
    data_processamento = models.DateTimeField(null=True, blank=True)
    quantidade_registros = models.IntegerField(default=0)
    quantidade_pagos = models.IntegerField(default=0)
    quantidade_baixados = models.IntegerField(default=0)
    quantidade_rejeitados = models.IntegerField(default=0)
    valor_total_pago = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    processado = models.BooleanField(default=False)
    layout = models.CharField(max_length=10, blank=True)
    arquivo_hash = models.CharField(max_length=64, db_index=True)
    resumo_processamento = models.JSONField(default=dict, blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retornos_bancarios"
        constraints = [
            models.UniqueConstraint(
                fields=["filial", "arquivo_hash"], name="fin_retorno_hash_filial_uniq",
            ),
        ]


class RetornoBancarioItem(models.Model):
    retorno = models.ForeignKey(
        RetornoBancario, on_delete=models.CASCADE, related_name="itens",
    )
    boleto = models.ForeignKey(
        "Boleto", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ocorrencias_retorno",
    )
    numero_linha = models.PositiveIntegerField()
    nosso_numero = models.CharField(max_length=30, blank=True)
    codigo_ocorrencia = models.CharField(max_length=3, blank=True)
    descricao_ocorrencia = models.CharField(max_length=120, blank=True)
    data_ocorrencia = models.DateField(null=True, blank=True)
    valor_titulo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_tarifa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, default="pendente")
    mensagem = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "retornos_bancarios_itens"
        ordering = ["numero_linha"]
        constraints = [
            models.UniqueConstraint(
                fields=["retorno", "numero_linha"], name="fin_retorno_item_linha_uniq",
            ),
        ]


class Boleto(TimestampedModel):
    conta_receber = models.ForeignKey(ContaReceber, on_delete=models.CASCADE, related_name="boletos")
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT)
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    nosso_numero = models.CharField(max_length=20)
    seu_numero = models.CharField(max_length=20, blank=True)
    linha_digitavel = models.CharField(max_length=54, blank=True)
    codigo_barras = models.CharField(max_length=44, blank=True)
    carteira = models.CharField(max_length=4, blank=True)
    data_emissao = models.DateField()
    data_vencimento = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    instrucoes = models.TextField(blank=True)
    status = models.CharField(max_length=20, default="gerado")
    data_pagamento = models.DateField(null=True, blank=True)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    banco_codigo_retorno = models.CharField(max_length=3, blank=True)
    remessa = models.ForeignKey(RemessaBancaria, on_delete=models.SET_NULL, null=True, blank=True)
    retorno = models.ForeignKey(RetornoBancario, on_delete=models.SET_NULL, null=True, blank=True)
    url_boleto = models.URLField(max_length=500, blank=True)

    class Meta:
        db_table = "boletos"
        ordering = ["-created_at"]
