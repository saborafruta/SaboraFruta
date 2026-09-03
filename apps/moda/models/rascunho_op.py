from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils.text import get_valid_filename

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


def caminho_imagem_rascunho(instancia, nome_original):
    return (
        f'moda/rascunhos/{instancia.rascunho.chave}/'
        f'{uuid4()}-{get_valid_filename(nome_original)}'
    )


class ImagemRascunhoOP(models.Model):
    """Imagem persistida antes de a OP e seu item definitivo existirem."""

    rascunho = models.ForeignKey(
        RascunhoOP, on_delete=models.CASCADE, related_name='imagens',
    )
    item_uid = models.CharField(max_length=100, db_index=True)
    arquivo = models.ImageField(upload_to=caminho_imagem_rascunho)
    nome_original = models.CharField(max_length=255)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em', 'id']
        verbose_name = 'imagem do rascunho de OP'
        verbose_name_plural = 'imagens dos rascunhos de OP'
