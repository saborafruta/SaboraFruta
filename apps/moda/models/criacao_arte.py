from django.conf import settings
from django.db import models
from django.utils import timezone


class RegistroCriacaoArte(models.Model):
    """Registro imutável do que foi orientado ou combinado sobre a arte."""

    pedido = models.ForeignKey(
        'moda.PedidoProducao', on_delete=models.CASCADE,
        related_name='historico_criacao',
    )
    texto = models.TextField()
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='registros_criacao_arte',
    )
    criado_em = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = 'moda_criacao_arte_historico'
        ordering = ['-criado_em', '-pk']
        indexes = [
            models.Index(fields=['pedido', '-criado_em'], name='moda_criacao_pedido_data'),
        ]

    def __str__(self):
        return self.texto[:80]
