from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class Reserva(FilialScopedModel):
    """Reserva futura de mesa. `mesa` é opcional — pode reservar só a data/horário."""

    class Status(models.TextChoices):
        CONFIRMADA = 'confirmada', 'Confirmada'
        CANCELADA = 'cancelada', 'Cancelada'
        ATENDIDA = 'atendida', 'Atendida'

    mesa = models.ForeignKey(
        'food_service.Mesa', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservas',
    )
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservas_food_service',
    )
    nome_contato = models.CharField(max_length=100, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    data_hora = models.DateTimeField()
    quantidade_pessoas = models.PositiveIntegerField(default=2)
    observacoes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMADA)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'food_service_reservas'
        verbose_name = 'Reserva'
        verbose_name_plural = 'Reservas'
        ordering = ['data_hora']

    def __str__(self):
        quem = self.cliente.razao_social if self.cliente_id else self.nome_contato
        return f'{quem or "Reserva"} — {self.data_hora:%d/%m %H:%M}'
