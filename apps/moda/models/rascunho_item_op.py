from django.conf import settings
from django.db import models

from apps.core.models.base import FilialScopedModel


class RascunhoItemOP(FilialScopedModel):
    """Item incompleto preservado sem participar dos totais da OP."""

    pedido = models.OneToOneField(
        'moda.PedidoProducao',
        on_delete=models.CASCADE,
        related_name='rascunho_item',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rascunhos_item_op',
    )
    dados = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'rascunho de item da OP'
        verbose_name_plural = 'rascunhos de item da OP'
