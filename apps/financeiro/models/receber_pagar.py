"""Contas a receber e contas a pagar."""
from pathlib import Path
import uuid

from django.core.validators import FileExtensionValidator
from django.db import models
from apps.core.models import Filial, Usuario
from apps.cadastros.models import Cliente, Fornecedor
from apps.core.models.base import TimestampedModel
from apps.core.models.base import FilialManager as FilialAwareManager
from .formas_pagamento import FormaPagamento
from .conta_bancaria import ContaBancaria, PlanoContas
from ..constants.enums import StatusContaReceber, StatusContaPagar, StatusPIX


EXTENSOES_COMPROVANTE = ['pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif']


class ContaPagarManager(FilialAwareManager):
    """Oculta títulos excluídos das rotinas financeiras por padrão."""

    def get_queryset(self):
        return super().get_queryset().filter(excluido_em__isnull=True)


def caminho_comprovante_pagamento(instancia, nome_original):
    """Usa nome imprevisível e separa os comprovantes por filial."""
    extensao = Path(nome_original).suffix.lower()
    return f'financeiro/comprovantes/{instancia.filial_id}/{uuid.uuid4().hex}{extensao}'


class ContaReceber(TimestampedModel):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="contas_receber")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="contas_receber")
    documento_tipo = models.CharField(max_length=30, blank=True)
    documento_id = models.BigIntegerField(null=True, blank=True)
    documento_numero = models.CharField(max_length=20, blank=True)
    parcela = models.PositiveSmallIntegerField(default=1)
    total_parcelas = models.PositiveSmallIntegerField(default=1)

    valor_original = models.DecimalField(max_digits=14, decimal_places=2)
    valor_juros = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_multa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_final = models.DecimalField(max_digits=14, decimal_places=2)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_saldo = models.DecimalField(max_digits=14, decimal_places=2)
    taxa_percentual_aplicada = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    taxa_fixa_aplicada = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_taxa_recebimento = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_liquido_recebido = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxa_calculada_em = models.DateTimeField(null=True, blank=True)

    data_emissao = models.DateField()
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(null=True, blank=True)
    prazo_compensacao_aplicado = models.PositiveSmallIntegerField(default=0)
    data_liquidacao_prevista = models.DateField(null=True, blank=True, db_index=True)

    forma_pagamento = models.ForeignKey(FormaPagamento, on_delete=models.SET_NULL, null=True, blank=True)
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.SET_NULL, null=True, blank=True)
    plano_contas = models.ForeignKey(PlanoContas, on_delete=models.SET_NULL, null=True, blank=True)
    conta_contabil = models.ForeignKey(
        "financeiro.PlanoContabil",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contas_receber_classificadas",
    )

    nosso_numero = models.CharField(max_length=20, blank=True)
    linha_digitavel = models.CharField(max_length=54, blank=True)
    codigo_barras = models.CharField(max_length=44, blank=True)
    boleto_url = models.URLField(max_length=500, blank=True)
    boleto_status = models.CharField(max_length=20, blank=True)

    pix_txid = models.CharField(max_length=35, blank=True)
    pix_qrcode = models.TextField(blank=True)
    pix_status = models.CharField(max_length=20, blank=True, choices=StatusPIX.choices)

    status = models.CharField(
        max_length=20, choices=StatusContaReceber.choices,
        default=StatusContaReceber.ABERTO,
    )
    competencia = models.DateField(null=True, blank=True)
    observacao = models.TextField(blank=True)

    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="contas_receber_emitidas")
    usuario_baixa = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="contas_receber_baixadas")

    objects = FilialAwareManager()

    class Meta:
        db_table = "contas_receber"
        verbose_name = "Conta a receber"
        verbose_name_plural = "Contas a receber"
        ordering = ["data_vencimento"]
        indexes = [
            models.Index(fields=["filial", "status", "data_vencimento"]),
            models.Index(fields=["filial", "cliente"]),
        ]

    def __str__(self):
        return f"CR {self.documento_numero}/{self.parcela} – {self.cliente}"

    @property
    def valor_entrada_liquida(self):
        if self.taxa_calculada_em:
            return self.valor_liquido_recebido
        return self.valor_pago


class ContaPagar(TimestampedModel):
    class TipoLancamento(models.TextChoices):
        FORNECEDOR = "fornecedor", "Fornecedor ou outro"
        FUNCIONARIO = "funcionario", "Pagamento ao funcionario"
        ENCARGO = "encargo", "Encargo ou beneficio"

    class FrequenciaRecorrencia(models.TextChoices):
        SEMANAL = "semanal", "Semanal"
        MENSAL = "mensal", "Mensal"
        TRIMESTRAL = "trimestral", "Trimestral"
        SEMESTRAL = "semestral", "Semestral"
        ANUAL = "anual", "Anual"

    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="contas_pagar")
    fornecedor = models.ForeignKey(Fornecedor, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name="contas_pagar")
    funcionario = models.ForeignKey(
        "cadastros.Funcionario", on_delete=models.PROTECT,
        null=True, blank=True, related_name="contas_pagar",
    )
    tipo_lancamento = models.CharField(
        max_length=12, choices=TipoLancamento.choices, default=TipoLancamento.FORNECEDOR,
    )
    documento_tipo = models.CharField(max_length=30, blank=True)
    documento_id = models.BigIntegerField(null=True, blank=True)
    documento_numero = models.CharField(max_length=20, blank=True)
    nota_fiscal_fornecedor = models.CharField(max_length=20, blank=True)
    chave_acesso_nfe = models.CharField(max_length=44, blank=True, db_index=True)
    parcela = models.PositiveSmallIntegerField(default=1)
    total_parcelas = models.PositiveSmallIntegerField(default=1)
    grupo_recorrencia = models.UUIDField(null=True, blank=True, db_index=True)
    frequencia_recorrencia = models.CharField(
        max_length=12, choices=FrequenciaRecorrencia.choices, blank=True,
    )

    valor_original = models.DecimalField(max_digits=14, decimal_places=2)
    valor_juros = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_multa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_final = models.DecimalField(max_digits=14, decimal_places=2)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_saldo = models.DecimalField(max_digits=14, decimal_places=2)

    data_emissao = models.DateField()
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(null=True, blank=True)
    data_competencia = models.DateField(null=True, blank=True)
    ajustar_vencimento_dia_util = models.BooleanField(default=False)

    forma_pagamento = models.ForeignKey(
        FormaPagamento, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    forma_pagamento_prevista = models.ForeignKey(
        FormaPagamento, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.SET_NULL, null=True, blank=True)
    plano_contas = models.ForeignKey(PlanoContas, on_delete=models.SET_NULL, null=True, blank=True)
    conta_contabil = models.ForeignKey(
        "financeiro.PlanoContabil",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="contas_pagar_classificadas",
    )

    status = models.CharField(
        max_length=20, choices=StatusContaPagar.choices, default=StatusContaPagar.ABERTO,
    )
    comprovante_url = models.URLField(max_length=500, blank=True)
    observacao = models.TextField(blank=True)

    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="contas_pagar_emitidas")
    usuario_pagamento = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name="contas_pagar_pagas")

    excluido_em = models.DateTimeField(null=True, blank=True, db_index=True)
    excluido_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contas_pagar_excluidas",
    )
    motivo_exclusao = models.CharField(max_length=300, blank=True)

    objects = ContaPagarManager()
    all_objects = FilialAwareManager()

    class Meta:
        db_table = "contas_pagar"
        verbose_name = "Conta a pagar"
        verbose_name_plural = "Contas a pagar"
        ordering = ["data_vencimento"]
        indexes = [
            models.Index(fields=["filial", "status", "data_vencimento"]),
            models.Index(fields=["filial", "fornecedor"]),
            models.Index(fields=["filial", "funcionario"]),
            models.Index(fields=["filial", "grupo_recorrencia"]),
        ]

    def __str__(self):
        return f"CP {self.documento_numero}/{self.parcela}"

    @property
    def beneficiario_nome(self):
        if self.funcionario_id:
            return self.funcionario.nome
        if self.fornecedor_id:
            return str(self.fornecedor)
        if self.tipo_lancamento == self.TipoLancamento.ENCARGO:
            return "Encargo trabalhista"
        return "Sem beneficiario"

    @property
    def beneficiario_documento(self):
        if self.funcionario_id:
            return self.funcionario.cpf
        if self.fornecedor_id:
            return self.fornecedor.cpf_cnpj
        return ""

    @property
    def excluido(self):
        return self.excluido_em is not None


class PagamentoContaPagar(TimestampedModel):
    """Movimento individual de baixa de uma conta a pagar."""

    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name='pagamentos_contas_pagar')
    conta_pagar = models.ForeignKey(ContaPagar, on_delete=models.CASCADE, related_name='pagamentos')
    data_pagamento = models.DateField()
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2)
    valor_juros = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_multa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    forma_pagamento = models.ForeignKey(
        FormaPagamento, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    conta_bancaria = models.ForeignKey(
        ContaBancaria, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    referencia_pagamento = models.CharField(max_length=100, blank=True)
    comprovante_url = models.URLField(max_length=500, blank=True)
    comprovante_arquivo = models.FileField(
        upload_to=caminho_comprovante_pagamento,
        validators=[FileExtensionValidator(allowed_extensions=EXTENSOES_COMPROVANTE)],
        max_length=500,
        blank=True,
    )
    comprovante_nome_original = models.CharField(max_length=255, blank=True)
    observacao = models.TextField(blank=True)
    usuario = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pagamentos_contas_pagar_registrados',
    )

    objects = FilialAwareManager()

    class Meta:
        db_table = 'pagamentos_contas_pagar'
        ordering = ['-data_pagamento', '-created_at']
        indexes = [
            models.Index(fields=['filial', 'data_pagamento']),
            models.Index(fields=['conta_pagar', 'data_pagamento']),
        ]

    @property
    def valor_liquido(self):
        return self.valor_pago + self.valor_juros + self.valor_multa - self.valor_desconto

    def __str__(self):
        return f'Pagamento CP #{self.conta_pagar_id} em {self.data_pagamento:%d/%m/%Y}'
