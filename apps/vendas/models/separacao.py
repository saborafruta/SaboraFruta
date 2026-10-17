"""Separação de pedido — processo intermediário antes do faturamento."""
from django.db import models
from django.db.models import Q

from apps.core.models.base import FilialScopedModel, TimestampedModel


class SeparacaoPedido(FilialScopedModel):
    """Documento de separação gerado para um pedido confirmado."""

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Aberta'
        EM_ANDAMENTO = 'em_andamento', 'Em Andamento'
        CONCLUIDA = 'concluida', 'Concluída'
        CANCELADA = 'cancelada', 'Cancelada'

    pedido = models.ForeignKey(
        'vendas.PedidoVenda', on_delete=models.PROTECT, related_name='separacoes',
    )
    numero = models.CharField(max_length=20)
    idempotency_key = models.CharField(
        max_length=64,
        blank=True,
        help_text='Identificador único da conferência enviada pelo aplicativo.',
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ABERTA,
    )
    usuario_separador = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT,
        related_name='separacoes_realizadas',
    )
    data_inicio = models.DateTimeField(auto_now_add=True)
    data_fim = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True)

    class Meta:
        db_table = 'separacoes_pedido'
        ordering = ['-data_inicio']
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'idempotency_key'],
                condition=~Q(idempotency_key=''),
                name='separacao_idempotencia_filial_unica',
            ),
        ]

    def __str__(self):
        return f'Separação {self.numero} — {self.pedido.numero_pedido}'


class ItemSeparacao(TimestampedModel):
    """Item separado com lote específico (respeitando FEFO)."""

    separacao = models.ForeignKey(
        SeparacaoPedido, on_delete=models.CASCADE, related_name='itens',
    )
    item_pedido = models.ForeignKey(
        'vendas.ItemPedidoVenda', on_delete=models.PROTECT, related_name='separacoes',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.PROTECT, related_name='+',
    )
    quantidade_separada = models.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        db_table = 'itens_separacao'
