"""
Capacidade produtiva por setor.

Existe porque "capacidade disponível" não estava em lugar nenhum. A
`Operacao` guarda capacidade em PEÇAS POR HORA, que é vazão e depende do
produto: 40 peças/hora de camisa simples não são 40 peças/hora de conjunto
bordado. Comparar isso com a carga de um mix de produtos daria um número
sem significado.

Aqui a conta é em MINUTOS, que é a moeda comum: o roteiro diz quantos
minutos cada peça consome em cada setor, e este cadastro diz quantos
minutos o setor tem por semana. As duas pontas na mesma unidade é o que
torna "capacidade × carga" uma comparação honesta.
"""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

from .roteiro import Operacao

MINUTOS_POR_HORA = Decimal('60')


class CapacidadeSetor(FilialScopedModel):
    """Quantos minutos por semana um setor entrega."""

    setor = models.CharField(max_length=20, choices=Operacao.Setor.choices)

    postos = models.PositiveSmallIntegerField(
        default=1,
        help_text='Quantas pessoas ou máquinas trabalham em paralelo neste setor.',
    )
    horas_dia = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('8'),
        verbose_name='Horas por dia',
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('24'))],
    )
    dias_semana = models.PositiveSmallIntegerField(
        default=5, verbose_name='Dias por semana',
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )

    # Eficiência real do setor. Ninguém produz 100% da jornada: há troca de
    # peça, ajuste de máquina, parada. Planejar com 100% é como o plano
    # estoura na primeira semana sem ninguém entender por quê.
    eficiencia = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('85'),
        verbose_name='Eficiência (%)',
        help_text='Percentual da jornada realmente produtivo. 85% é um ponto de partida comum.',
        validators=[MinValueValidator(Decimal('1')), MaxValueValidator(Decimal('100'))],
    )

    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_capacidade_setor'
        ordering = ['setor']
        unique_together = [('filial', 'setor')]
        verbose_name = 'Capacidade do setor'
        verbose_name_plural = 'Capacidade dos setores'

    def __str__(self):
        return f'{self.get_setor_display()} — {self.minutos_semana:.0f} min/semana'

    @property
    def minutos_dia(self) -> Decimal:
        bruto = Decimal(self.postos) * (self.horas_dia or Decimal('0')) * MINUTOS_POR_HORA
        return (bruto * (self.eficiencia or Decimal('0')) / Decimal('100')).quantize(Decimal('0.01'))

    @property
    def minutos_semana(self) -> Decimal:
        return (self.minutos_dia * Decimal(self.dias_semana)).quantize(Decimal('0.01'))

    @property
    def horas_semana(self) -> Decimal:
        return (self.minutos_semana / MINUTOS_POR_HORA).quantize(Decimal('0.1'))
