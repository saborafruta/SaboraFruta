from decimal import Decimal

from django.db import models

from apps.core.models.base import TimestampedModel


class ComplementoItemComanda(TimestampedModel):
    """
    Adicional lançado sobre um item da comanda (ex.: queijo extra, molho à
    parte). É um `Produto` normal do cadastro — reaproveita preço e, ao
    fechar a comanda, vira sua própria linha na venda (mesmo tratamento de
    estoque/fiscal de qualquer outro item vendido).
    """

    item = models.ForeignKey('food_service.ItemComanda', on_delete=models.CASCADE, related_name='complementos')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1'))
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        db_table = 'food_service_complementos_item'
        verbose_name = 'Complemento de item'
        verbose_name_plural = 'Complementos de item'
        ordering = ['id']

    def __str__(self):
        return f'{self.quantidade}x {self.produto} (item #{self.item_id})'

    @property
    def valor_total(self):
        return self.quantidade * self.valor_unitario
