"""Cotacao inteligente com snapshots fiscais e comerciais auditaveis."""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.constants.tributacao import RegimeIBSCBS
from apps.core.models.base import FilialScopedModel, TimestampedModel


REGIMES_EMPRESARIAIS = (
    ('mei', 'MEI'),
    ('simples_nacional', 'Simples Nacional'),
    ('lucro_presumido', 'Lucro Presumido'),
    ('lucro_real', 'Lucro Real'),
)


class RegraTributariaCompra(TimestampedModel):
    """Regra parametrizada; nenhuma aliquota e inferida no componente de tela."""

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='regras_tributarias_compra',
    )
    nome = models.CharField(max_length=120)
    data_inicial = models.DateField(db_index=True)
    data_final = models.DateField(null=True, blank=True, db_index=True)
    regime_comprador = models.CharField(max_length=20, choices=REGIMES_EMPRESARIAIS, blank=True)
    regime_fornecedor = models.CharField(max_length=20, choices=REGIMES_EMPRESARIAIS, blank=True)
    regime_ibs_cbs_comprador = models.CharField(max_length=10, choices=RegimeIBSCBS.choices, blank=True)
    regime_ibs_cbs_fornecedor = models.CharField(max_length=10, choices=RegimeIBSCBS.choices, blank=True)
    classe_fiscal = models.ForeignKey(
        'produtos.ClasseFiscal', on_delete=models.PROTECT, null=True, blank=True,
        related_name='regras_credito_compra',
        help_text='Opcional. Reutiliza a classificacao fiscal ja vinculada ao produto.',
    )
    ncm_prefixo = models.CharField(
        max_length=8, blank=True,
        help_text='Opcional. Quanto maior o prefixo, mais especifica e a regra.',
    )
    percentual_credito_ibs = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    percentual_credito_cbs = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    percentual_outros_creditos = models.DecimalField(
        max_digits=7, decimal_places=4, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    condicoes = models.JSONField(default=dict, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'regras_tributarias_compra'
        ordering = ['-data_inicial', '-pk']
        indexes = [models.Index(fields=['empresa', 'ativo', 'data_inicial'])]
        verbose_name = 'Regra tributaria de compra'
        verbose_name_plural = 'Regras tributarias de compra'

    def __str__(self):
        return self.nome


class CotacaoCompra(FilialScopedModel):
    class Status(models.TextChoices):
        ANALISADA = 'analisada', 'Analisada'

    class Visao(models.TextChoices):
        MENOR_PRECO = 'menor_preco', 'Menor preco nominal'
        MAIOR_CREDITO = 'maior_credito', 'Maior credito tributario'
        MENOR_CUSTO = 'menor_custo', 'Menor custo efetivo'

    numero = models.CharField(max_length=24, db_index=True)
    usuario = models.ForeignKey('core.Usuario', on_delete=models.PROTECT, related_name='cotacoes_compra')
    data_referencia = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ANALISADA)
    visao_padrao = models.CharField(max_length=20, choices=Visao.choices, default=Visao.MENOR_CUSTO)
    regime_comprador = models.CharField(max_length=20, choices=REGIMES_EMPRESARIAIS)
    regime_ibs_cbs_comprador = models.CharField(max_length=10, choices=RegimeIBSCBS.choices)
    empresa_snapshot = models.JSONField(default=dict)
    valor_nominal_total = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    creditos_estimados_total = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    custo_efetivo_total = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    economia_estimada = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    fornecedor_base_nome = models.CharField(max_length=150, blank=True)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'cotacoes_compra'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['filial', '-created_at'])]
        verbose_name = 'Cotacao de compra'
        verbose_name_plural = 'Cotacoes de compra'

    def __str__(self):
        return self.numero


class CotacaoCompraItem(TimestampedModel):
    cotacao = models.ForeignKey(CotacaoCompra, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='cotacoes_compra')
    produto_descricao = models.CharField(max_length=150)
    produto_codigo = models.CharField(max_length=30, blank=True)
    produto_ncm = models.CharField(max_length=8, blank=True)
    produto_fiscal_snapshot = models.JSONField(default=dict, blank=True)
    quantidade = models.DecimalField(max_digits=14, decimal_places=3)
    unidade_sigla = models.CharField(max_length=12, blank=True)

    class Meta:
        db_table = 'cotacoes_compra_itens'
        ordering = ['pk']
        unique_together = [('cotacao', 'produto')]


class CotacaoCompraFornecedor(TimestampedModel):
    cotacao = models.ForeignKey(CotacaoCompra, on_delete=models.CASCADE, related_name='fornecedores')
    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='participacoes_cotacao',
    )
    manual_supplier = models.BooleanField(default=False)
    supplier_name = models.CharField(max_length=150)
    supplier_cnpj = models.CharField(max_length=14, blank=True)
    supplier_tax_regime = models.CharField(max_length=20, choices=REGIMES_EMPRESARIAIS)
    supplier_tax_ibs_cbs = models.CharField(max_length=10, choices=RegimeIBSCBS.choices)
    supplier_snapshot = models.JSONField(default=dict, blank=True)
    salvo_no_cadastro = models.BooleanField(default=False)

    class Meta:
        db_table = 'cotacoes_compra_fornecedores'
        ordering = ['pk']

    def __str__(self):
        return self.supplier_name


class CotacaoCompraPreco(TimestampedModel):
    item = models.ForeignKey(CotacaoCompraItem, on_delete=models.CASCADE, related_name='precos')
    fornecedor = models.ForeignKey(CotacaoCompraFornecedor, on_delete=models.CASCADE, related_name='precos')
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    frete_nao_recuperavel = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    desconto = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    condicoes_comerciais = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'cotacoes_compra_precos'
        unique_together = [('item', 'fornecedor')]


class CalculoTributarioCompra(TimestampedModel):
    preco = models.OneToOneField(CotacaoCompraPreco, on_delete=models.CASCADE, related_name='calculo')
    regra = models.ForeignKey(
        RegraTributariaCompra, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='calculos',
    )
    valor_bruto = models.DecimalField(max_digits=18, decimal_places=4)
    custos_nao_recuperaveis = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    credito_ibs = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    credito_cbs = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    outros_creditos = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    credito_total = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    custo_efetivo = models.DecimalField(max_digits=18, decimal_places=4)
    regra_snapshot = models.JSONField(default=dict, blank=True)
    vencedor = models.BooleanField(default=False, db_index=True)
    posicao = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = 'calculos_tributarios_compra'
        ordering = ['posicao', 'custo_efetivo']
