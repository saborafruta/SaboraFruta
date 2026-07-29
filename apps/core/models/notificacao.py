from django.conf import settings
from django.db import models

from apps.core.models.base import TimestampedModel


class Notificacao(TimestampedModel):
    class Tipo(models.TextChoices):
        TRANSFERENCIA_RECEBIDA = 'transferencia_recebida', 'Transferencia recebida'
        TRANSFERENCIA_CONFERIDA = 'transferencia_conferida', 'Transferencia conferida'
        ALERTA_SISTEMA = 'alerta_sistema', 'Alerta do sistema'

    filial = models.ForeignKey(
        'core.Filial',
        on_delete=models.CASCADE,
        related_name='notificacoes',
    )
    tipo = models.CharField(max_length=40, choices=Tipo.choices, db_index=True)
    titulo = models.CharField(max_length=140)
    mensagem = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True)
    referencia_tipo = models.CharField(max_length=50, blank=True)
    referencia_id = models.CharField(max_length=80, blank=True)
    ativa = models.BooleanField(default=True, db_index=True)
    lida_por = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='NotificacaoLeitura',
        related_name='notificacoes_lidas',
        blank=True,
    )

    class Meta:
        db_table = 'notificacoes'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['filial', 'ativa', '-created_at']),
            models.Index(fields=['referencia_tipo', 'referencia_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'tipo', 'referencia_tipo', 'referencia_id'],
                name='notificacao_referencia_unica',
                condition=~models.Q(referencia_id=''),
            ),
        ]

    def __str__(self):
        return self.titulo


class NotificacaoLeitura(models.Model):
    notificacao = models.ForeignKey(
        Notificacao,
        on_delete=models.CASCADE,
        related_name='leituras',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leituras_notificacoes',
    )
    lida_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notificacoes_leituras'
        constraints = [
            models.UniqueConstraint(
                fields=['notificacao', 'usuario'],
                name='notificacao_leitura_usuario_unica',
            ),
        ]
