from decimal import Decimal

from django.db import models

from apps.core.models.base import TimestampedModel


class PedidoPendente(TimestampedModel):
    """
    Pedido montado pelo cliente no Cardápio Digital (QR Code).

    Não é `FilialScopedModel` -- a filial é sempre lida via `mesa.filial`,
    mesmo padrão de `ItemComanda`. Fica "pendente" até um garçom confirmar
    (`PedidoPendenteService.confirmar_pedido`), que só então cria os
    `ItemComanda`/`ComplementoItemComanda` de verdade (com preço resolvido
    e baixa de estoque). Enquanto pendente, não afeta nada operacional --
    é a fronteira de segurança do acesso público sem login.
    """

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        CONFIRMADO = 'confirmado', 'Confirmado'
        RECUSADO = 'recusado', 'Recusado'

    mesa = models.ForeignKey('food_service.Mesa', on_delete=models.CASCADE, related_name='pedidos_pendentes')
    comanda = models.ForeignKey(
        'food_service.Comanda', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pedidos_pendentes',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    observacoes = models.TextField(blank=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
    confirmado_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    recusado_motivo = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'food_service_pedidos_pendentes'
        verbose_name = 'Pedido pendente'
        verbose_name_plural = 'Pedidos pendentes'
        ordering = ['-created_at']

    def __str__(self):
        return f'Pedido pendente #{self.pk} (mesa {self.mesa_id})'


class ItemPedidoPendente(models.Model):
    """
    Sem `valor_unitario` de propósito -- preço nunca é confiado vindo do
    público; é resolvido de verdade só na confirmação, pela mesma fonte que
    `ComandaService.adicionar_item` já usa.
    """

    pedido = models.ForeignKey(PedidoPendente, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1'))
    observacoes = models.TextField(blank=True)

    class Meta:
        db_table = 'food_service_itens_pedido_pendente'
        ordering = ['id']

    def __str__(self):
        return f'{self.quantidade}x {self.produto} (pedido #{self.pedido_id})'


class ComplementoItemPedidoPendente(models.Model):
    item = models.ForeignKey(ItemPedidoPendente, on_delete=models.CASCADE, related_name='complementos')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    quantidade = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('1'))

    class Meta:
        db_table = 'food_service_complementos_pedido_pendente'

    def __str__(self):
        return f'{self.quantidade}x {self.produto} (item #{self.item_id})'
