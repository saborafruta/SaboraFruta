from uuid import uuid4

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
    chave = models.UUIDField(default=uuid4, unique=True, editable=False)
    dados = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = 'rascunho de OP'
        verbose_name_plural = 'rascunhos de OP'
