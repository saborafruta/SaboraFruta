from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel


class Mesa(FilialScopedModel, ActiveModel):
    """
    Cadastro estático de uma mesa do salão.

    Dados de ocupação (garçom, cliente, horário de abertura, valor consumido,
    quantidade de pessoas) NÃO ficam aqui — são lidos das Comandas abertas
    vinculadas à mesa, para não duplicar informação que dessincroniza.
    `status` é a única exceção: como uma Comanda pode cobrir várias mesas
    (união de mesas), o status não é derivável de uma única FK.
    """

    class Status(models.TextChoices):
        LIVRE = 'livre', 'Livre'
        OCUPADA = 'ocupada', 'Ocupada'
        RESERVADA = 'reservada', 'Reservada'
        AGUARDANDO_LIMPEZA = 'aguardando_limpeza', 'Aguardando limpeza'
        FECHANDO_CONTA = 'fechando_conta', 'Fechando conta'

    numero = models.PositiveIntegerField()
    nome = models.CharField(max_length=60, blank=True)
    capacidade = models.PositiveIntegerField(default=4)
    setor = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.LIVRE)
    observacoes = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'food_service_mesas'
        verbose_name = 'Mesa'
        verbose_name_plural = 'Mesas'
        constraints = [
            models.UniqueConstraint(fields=['filial', 'numero'], name='uq_mesa_filial_numero'),
        ]
        ordering = ['numero']

    def __str__(self):
        return self.nome or f'Mesa {self.numero}'
