"""Codigos de barras e equivalencias de compra por fornecedor."""
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models.base import TimestampedModel


class ProdutoCodigoBarras(TimestampedModel):
    class Tipo(models.TextChoices):
        UNIDADE = 'unidade', 'Unidade'
        CAIXA = 'caixa', 'Caixa'
        PACOTE = 'pacote', 'Pacote'
        FORNECEDOR = 'fornecedor', 'Fornecedor'
        ALTERNATIVO = 'alternativo', 'Alternativo'

    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.CASCADE,
        related_name='codigos_barras',
    )
    apresentacao = models.ForeignKey(
        'produtos.ProdutoApresentacao',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='codigos_barras',
        help_text='Apresentacao a que este EAN pertence. Opcional: nulo para codigos ainda nao migrados/vinculados.',
    )
    ean = models.CharField(max_length=32, db_index=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.ALTERNATIVO)
    quantidade_conversao = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    ativo = models.BooleanField(default=True, db_index=True)
    observacao = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'produtos_codigos_barras'
        ordering = ['produto', 'ean']
        indexes = [
            models.Index(fields=['ean', 'ativo'], name='prod_cod_barras_ean_ativo_idx'),
            models.Index(fields=['produto', 'ativo'], name='prod_cod_barras_prod_ativo_idx'),
            models.Index(fields=['apresentacao', 'ativo'], name='pcb_apresentacao_ativo_idx'),
        ]
        verbose_name = 'Codigo de barras do produto'
        verbose_name_plural = 'Codigos de barras dos produtos'

    def __str__(self):
        return f'{self.ean} - {self.produto}'

    def clean(self):
        super().clean()
        ean = (self.ean or '').strip()
        if self.ativo and ean and self.produto_id:
            duplicado = ProdutoCodigoBarras.objects.filter(
                produto_id=self.produto_id, ean=ean, ativo=True,
            ).exclude(pk=self.pk).exists()
            if duplicado:
                raise ValidationError({
                    'ean': 'Ja existe um codigo de barras ativo igual para este produto.',
                })


class ProdutoFornecedorEquivalencia(TimestampedModel):
    class Origem(models.TextChoices):
        XML = 'xml', 'XML'
        MANUAL = 'manual', 'Manual'
        MANIFESTO = 'manifesto', 'Manifesto'

    fornecedor = models.ForeignKey(
        'cadastros.Fornecedor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='equivalencias_produtos',
    )
    fornecedor_cnpj_xml = models.CharField(max_length=18, blank=True, db_index=True)
    fornecedor_razao_social_xml = models.CharField(max_length=180, blank=True)
    produto = models.ForeignKey(
        'produtos.Produto',
        on_delete=models.CASCADE,
        related_name='equivalencias_fornecedor',
    )
    codigo_fornecedor = models.CharField(max_length=80, blank=True, db_index=True)
    descricao_fornecedor = models.CharField(max_length=255, blank=True)
    ean_utilizado = models.CharField(max_length=32, blank=True, db_index=True)
    unidade_compra = models.CharField(max_length=10, blank=True)
    unidade_estoque = models.CharField(max_length=10, blank=True)
    fator_conversao = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    ultimo_custo = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    data_ultima_compra = models.DateField(null=True, blank=True)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.MANUAL)
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'produtos_fornecedores_equivalencias'
        ordering = ['fornecedor_razao_social_xml', 'codigo_fornecedor', 'ean_utilizado']
        indexes = [
            models.Index(fields=['fornecedor', 'ativo'], name='prod_forn_eq_fornecedor_idx'),
            models.Index(fields=['fornecedor_cnpj_xml', 'ativo'], name='prod_forn_eq_cnpj_idx'),
            models.Index(fields=['ean_utilizado', 'ativo'], name='prod_forn_eq_ean_idx'),
            models.Index(fields=['codigo_fornecedor', 'ativo'], name='prod_forn_eq_codigo_idx'),
        ]
        verbose_name = 'Equivalencia produto fornecedor'
        verbose_name_plural = 'Equivalencias produto fornecedor'

    def __str__(self):
        origem = self.fornecedor or self.fornecedor_razao_social_xml or self.fornecedor_cnpj_xml
        return f'{origem} -> {self.produto}'
