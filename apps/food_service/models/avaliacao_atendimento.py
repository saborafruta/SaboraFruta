from django.db import models

from apps.core.models.base import TimestampedModel


class AvaliacaoAtendimento(TimestampedModel):
    """Avaliação do cliente após o fechamento da comanda. Uma por comanda."""

    comanda = models.OneToOneField('food_service.Comanda', on_delete=models.CASCADE, related_name='avaliacao')
    nota = models.PositiveSmallIntegerField()
    comentario = models.TextField(blank=True)

    class Meta:
        db_table = 'food_service_avaliacoes_atendimento'
        verbose_name = 'Avaliação de atendimento'
        verbose_name_plural = 'Avaliações de atendimento'

    def __str__(self):
        return f'Avaliação da comanda #{self.comanda_id}: {self.nota}/5'
