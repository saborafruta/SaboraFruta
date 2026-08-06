from django.db import models

from apps.core.models.base import TimestampedModel


class ChamadoMesa(TimestampedModel):
    """Chamado do cliente pelo Cardápio Digital: chamar garçom ou pedir a conta."""

    class Tipo(models.TextChoices):
        GARCOM = 'garcom', 'Chamar garçom'
        CONTA = 'conta', 'Pedir a conta'

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        ATENDIDO = 'atendido', 'Atendido'

    mesa = models.ForeignKey('food_service.Mesa', on_delete=models.CASCADE, related_name='chamados')
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    atendido_em = models.DateTimeField(null=True, blank=True)
    atendido_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        db_table = 'food_service_chamados_mesa'
        verbose_name = 'Chamado de mesa'
        verbose_name_plural = 'Chamados de mesa'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_tipo_display()} — mesa {self.mesa_id}'
