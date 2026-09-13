"""Faixas de classificação de cobertura de estoque, configuráveis em cascata."""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models.base import TimestampedModel


class FaixaCoberturaEstoque(TimestampedModel):
    """
    Limites (em dias de cobertura) que classificam o saldo de um produto em
    Ruptura/Crítico/Baixo/Normal/Alto/Excesso.

    Resolução em cascata: produto > categoria > padrão da empresa (linha sem
    produto nem categoria). Sem nenhum registro, o sistema usa os limites
    padrão embutidos (3/7/15/30 dias) -- configurar aqui é opcional.
    """

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='faixas_cobertura_estoque',
    )
    categoria = models.ForeignKey(
        'produtos.CategoriaProduto', on_delete=models.CASCADE, null=True, blank=True,
        related_name='faixas_cobertura_estoque',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.CASCADE, null=True, blank=True,
        related_name='faixas_cobertura_estoque',
    )
    dias_critico = models.PositiveSmallIntegerField(default=3, help_text='Cobertura até este dia: Crítico')
    dias_baixo = models.PositiveSmallIntegerField(default=7, help_text='Cobertura até este dia: Baixo')
    dias_normal = models.PositiveSmallIntegerField(default=15, help_text='Cobertura até este dia: Normal')
    dias_alto = models.PositiveSmallIntegerField(default=30, help_text='Cobertura até este dia: Alto; acima: Excesso')

    class Meta:
        db_table = 'estoque_faixas_cobertura'
        ordering = ['empresa_id', 'categoria_id', 'produto_id']
        constraints = [
            models.UniqueConstraint(
                fields=['empresa'], condition=Q(categoria__isnull=True, produto__isnull=True),
                name='faixa_cobertura_padrao_unica_por_empresa',
            ),
            models.UniqueConstraint(
                fields=['empresa', 'categoria'], condition=Q(produto__isnull=True),
                name='faixa_cobertura_unica_por_categoria',
            ),
            models.UniqueConstraint(
                fields=['empresa', 'produto'], condition=Q(produto__isnull=False),
                name='faixa_cobertura_unica_por_produto',
            ),
        ]
        verbose_name = 'Faixa de cobertura de estoque'
        verbose_name_plural = 'Faixas de cobertura de estoque'

    def __str__(self):
        if self.produto_id:
            return f'{self.produto} (produto)'
        if self.categoria_id:
            return f'{self.categoria} (categoria)'
        return f'{self.empresa} (padrão da empresa)'

    def clean(self):
        if self.produto_id and self.categoria_id:
            raise ValidationError('Escolha produto ou categoria, não os dois.')
        limites = [self.dias_critico, self.dias_baixo, self.dias_normal, self.dias_alto]
        if limites != sorted(limites):
            raise ValidationError('Os limites devem crescer: crítico < baixo < normal < alto.')
