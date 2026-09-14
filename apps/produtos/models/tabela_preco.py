"""Tabelas de Preço com vigência e preço escalonado."""
from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models.base import FilialManager, FilialScopedModel, TimestampedModel
from .produto import Produto


class TabelaPrecoManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial=filial,
            filiais_vinculo__ativo=True,
        ).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial__empresa=empresa,
            filiais_vinculo__ativo=True,
        ).distinct()


class TabelaPreco(FilialScopedModel):
    class Tipo(models.TextChoices):
        VAREJO = 'varejo', 'Varejo'
        ATACADO = 'atacado', 'Atacado'
        EXPORTACAO = 'exportacao', 'Exportação'
        ESPECIAL = 'especial', 'Especial'
        FUNCIONARIO = 'funcionario', 'Funcionário'

    descricao = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.VAREJO)
    data_inicio = models.DateField(null=True, blank=True)
    data_fim = models.DateField(null=True, blank=True, help_text='NULL = sem prazo')
    permite_desconto = models.BooleanField(default=True)
    desconto_maximo_geral = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    acrescimo_percentual = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text='Para tabelas com acréscimo (ex: prazo longo)',
    )
    ativo = models.BooleanField(default=True, db_index=True)

    objects = TabelaPrecoManager()

    class Meta:
        db_table = 'tabelas_preco'
        ordering = ['descricao']
        verbose_name = 'Tabela de Preço'
        verbose_name_plural = 'Tabelas de Preço'

    def __str__(self):
        return f'{self.descricao} ({self.get_tipo_display()})'


class TabelaPrecoFilial(TimestampedModel):
    tabela = models.ForeignKey(TabelaPreco, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='tabelas_preco_vinculadas')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'tabelas_preco_filiais'
        ordering = ['tabela', 'filial']
        unique_together = [('tabela', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['tabela', 'ativo']),
        ]

    def __str__(self):
        return f'{self.tabela} - {self.filial}'


class ItemTabelaPreco(TimestampedModel):
    """Preço do produto na tabela com preço escalonado por quantidade mínima."""

    tabela = models.ForeignKey(TabelaPreco, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='precos_tabela')
    apresentacao = models.ForeignKey(
        'produtos.ProdutoApresentacao', on_delete=models.CASCADE, null=True, blank=True,
        related_name='precos_tabela',
        help_text='Nulo = preco no nivel do produto (comportamento historico). '
                   'Preenchido = preco especifico desta apresentacao nesta tabela (== nesta filial, '
                   'via TabelaPrecoFilial), independente do preco_venda da apresentacao.',
    )
    preco_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    desconto_maximo = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    desconto_valor = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text='Desconto máximo em R$ (valor), alternativo ao percentual.',
    )
    quantidade_minima = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
        help_text='Preço escalonado: a partir de X unidades',
    )

    class Meta:
        db_table = 'itens_tabela_preco'
        ordering = ['produto', 'quantidade_minima']
        constraints = [
            # NULL nao colide com NULL numa UniqueConstraint padrao (Postgres
            # permite varias linhas NULL) -- por isso duas constraints
            # condicionais em vez de um unique_together simples incluindo
            # `apresentacao`, que deixaria passar mais de um preco "default"
            # (apresentacao=None) para o mesmo produto/tabela/faixa.
            models.UniqueConstraint(
                fields=['tabela', 'produto', 'quantidade_minima'],
                condition=Q(apresentacao__isnull=True),
                name='uniq_item_tabela_preco_sem_apresentacao',
            ),
            models.UniqueConstraint(
                fields=['tabela', 'produto', 'apresentacao', 'quantidade_minima'],
                condition=Q(apresentacao__isnull=False),
                name='uniq_item_tabela_preco_com_apresentacao',
            ),
        ]

    @property
    def valor_final(self):
        """
        Preço efetivamente cobrado: o unitário já com o desconto em R$
        abatido. É este o valor que a venda usa — o `preco_unitario` é o
        valor "cheio" de referência. Nunca fica negativo.
        """
        preco = self.preco_unitario or Decimal('0')
        desconto = self.desconto_valor or Decimal('0')
        return max(preco - desconto, Decimal('0'))
