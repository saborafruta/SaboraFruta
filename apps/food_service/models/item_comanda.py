from decimal import Decimal

from django.db import models

from apps.core.models.base import TimestampedModel


class ItemComanda(TimestampedModel):
    """
    Item lançado numa comanda aberta.

    `valor_unitario` é um snapshot de EXIBIÇÃO (o preço mostrado ao
    garçom/cliente no momento do lançamento). O preço efetivamente cobrado é
    recalculado por `VendaPDVService._criar_item_e_baixar_estoque` quando a
    comanda fecha — a mesma fonte de preço (`resolver_preco_produto`) é
    usada nos dois momentos, então normalmente coincidem.
    """

    comanda = models.ForeignKey('food_service.Comanda', on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1'))
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    observacoes = models.TextField(blank=True)
    adicionado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'food_service_itens_comanda'
        verbose_name = 'Item de comanda'
        verbose_name_plural = 'Itens de comanda'
        ordering = ['adicionado_em']

    def __str__(self):
        return f'{self.quantidade}x {self.produto} (comanda #{self.comanda_id})'

    @property
    def valor_total(self):
        return self.quantidade * self.valor_unitario

    @property
    def valor_total_com_complementos(self):
        return self.valor_total + sum(
            (c.valor_total for c in self.complementos.all()), Decimal('0'),
        )
