"""Extrato bancário, conciliação e agenda de pagamentos."""
from django.db import models
from apps.core.models import Filial, Usuario
from apps.core.models.base import FilialManager as FilialAwareManager
from .conta_bancaria import ContaBancaria
from .receber_pagar import ContaPagar


class ExtratoBancario(models.Model):
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name="extratos")
    forma_pagamento = models.ForeignKey(
        "financeiro.FormaPagamento",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    data_lancamento = models.DateField()
    data_credito = models.DateField(null=True, blank=True)
    historico = models.CharField(max_length=200, blank=True)
    documento = models.CharField(max_length=30, blank=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2,
                                 help_text="Positivo=crédito Negativo=débito")
    saldo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    tipo_lancamento = models.CharField(max_length=20, blank=True)
    codigo_operacao = models.CharField(max_length=10, blank=True)
    origem = models.CharField(
        max_length=20, blank=True,
        help_text="ofx|cnab240|cnab400|api_banco|manual",
    )
    status = models.CharField(max_length=20, default="importado")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FilialAwareManager()

    class Meta:
        db_table = "extratos_bancarios"
        verbose_name = "Lançamento de extrato"
        verbose_name_plural = "Extratos bancários"
        ordering = ["-data_lancamento"]
        indexes = [models.Index(fields=["conta_bancaria", "data_lancamento"])]


class ConciliacaoBancaria(models.Model):
    extrato = models.ForeignKey(ExtratoBancario, on_delete=models.CASCADE, related_name="conciliacoes")
    lancamento_tipo = models.CharField(max_length=20)
    lancamento_id = models.BigIntegerField()
    diferenca = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    observacao = models.TextField(blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conciliacoes_bancarias"
        verbose_name = "Conciliação"
        verbose_name_plural = "Conciliações"


class AgendaPagamento(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    conta_pagar = models.ForeignKey(ContaPagar, on_delete=models.SET_NULL, null=True, blank=True)
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT)
    forma_pagamento = models.CharField(max_length=20, blank=True)
    favorecido_cpf_cnpj = models.CharField(max_length=14, blank=True)
    favorecido_nome = models.CharField(max_length=150, blank=True)
    chave_pix_destino = models.CharField(max_length=100, blank=True)
    codigo_barras = models.CharField(max_length=44, blank=True)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    data_agendamento = models.DateField()
    status = models.CharField(max_length=20, default="pendente")
    exige_aprovacao = models.BooleanField(default=False)
    aprovado_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="agendas_aprovadas",
    )
    aprovado_em = models.DateTimeField(null=True, blank=True)
    executado_em = models.DateTimeField(null=True, blank=True)
    response_banco = models.JSONField(null=True, blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="agendas_criadas")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agenda_pagamentos"
        ordering = ["data_agendamento"]
