from django.conf import settings
from django.db import models

from apps.core.models.base import FilialScopedModel


class RascunhoOP(FilialScopedModel):
    """Cópia recuperável de uma nova OP ainda incompleta."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='rascunhos_op',
    )
    dados = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('filial', 'usuario'),
                name='moda_rascunho_op_filial_usuario_unico',
            ),
        ]
        verbose_name = 'rascunho de OP'
        verbose_name_plural = 'rascunhos de OP'
