"""Extrato bancário, conciliação e agenda de pagamentos."""
from django.db import models
from django.utils import timezone
from apps.core.models import Filial, Usuario
from apps.core.models.base import FilialManager as FilialAwareManager
from .conta_bancaria import ContaBancaria, PlanoContas
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
    plano_contas = models.ForeignKey(
        PlanoContas,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extratos_classificados",
    )
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    data_lancamento = models.DateField()
    data_credito = models.DateField(null=True, blank=True)
    bandeira = models.CharField(max_length=20, blank=True, default="")
    numero_parcelas = models.PositiveSmallIntegerField(null=True, blank=True)
    taxa_percentual_aplicada = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    taxa_fixa_aplicada = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_taxa = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_liquido = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxa_calculada_em = models.DateTimeField(null=True, blank=True)
    prazo_compensacao_aplicado = models.PositiveSmallIntegerField(default=0)
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

    def recalcular_recebimento(self):
        """Congela taxa e prazo usados por uma entrada manual."""
        if (self.valor or 0) <= 0 or not self.forma_pagamento_id:
            self.taxa_percentual_aplicada = 0
            self.taxa_fixa_aplicada = 0
            self.valor_taxa = 0
            self.valor_liquido = max(self.valor or 0, 0)
            self.taxa_calculada_em = None
            self.prazo_compensacao_aplicado = 0
            self.data_credito = None
            return
        calculo = self.forma_pagamento.calcular_taxa_recebimento(
            self.valor,
            self.numero_parcelas or 1,
            self.bandeira,
        )
        self.taxa_percentual_aplicada = calculo["percentual"]
        self.taxa_fixa_aplicada = calculo["fixa"]
        self.valor_taxa = calculo["taxa"]
        self.valor_liquido = calculo["liquido"]
        self.taxa_calculada_em = timezone.now()
        self.prazo_compensacao_aplicado = self.forma_pagamento.prazo_compensacao_dias_uteis or 0
        from apps.core.services.calendario import adicionar_dias_uteis_bancarios
        self.data_credito = adicionar_dias_uteis_bancarios(
            self.data_lancamento,
            self.prazo_compensacao_aplicado,
            self.filial,
        )

    @property
    def valor_entrada_liquida(self):
        if (self.valor or 0) > 0 and self.taxa_calculada_em:
            return self.valor_liquido
        return self.valor


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
