"""Ajustes de meta de estoque por classe da curva ABC (giro)."""
from django.core.exceptions import ValidationError
from django.db import models


class ConfiguracaoAbcEstoque(models.Model):
    """
    Produto classe A gira mais e tolera menos ruptura -- este ajuste
    aplica um multiplicador sobre o mínimo/máximo cadastrado do produto e
    dias extras de cobertura na meta do equilíbrio, por classe (A/B/C).

    Sem linha cadastrada para uma classe, ela usa multiplicador 1 e zero
    dias extras -- ou seja, o comportamento de hoje, sem diferenciação.
    """

    class Classe(models.TextChoices):
        A = 'A', 'A — maior giro'
        B = 'B', 'B — giro intermediário'
        C = 'C', 'C — menor giro'

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='configuracoes_abc_estoque',
    )
    classe = models.CharField(max_length=1, choices=Classe.choices)
    multiplicador_minimo = models.DecimalField(
        max_digits=5, decimal_places=2, default=1,
        help_text='Multiplica o estoque mínimo cadastrado do produto (1 = sem alteração).',
    )
    multiplicador_maximo = models.DecimalField(
        max_digits=5, decimal_places=2, default=1,
        help_text='Multiplica o estoque máximo cadastrado do produto (1 = sem alteração).',
    )
    dias_cobertura_extra = models.SmallIntegerField(
        default=0,
        help_text='Dias somados à meta de cobertura do equilíbrio (positivo = mais reserva, negativo = menos).',
    )

    class Meta:
        db_table = 'estoque_configuracoes_abc'
        unique_together = [('empresa', 'classe')]
        ordering = ['empresa_id', 'classe']
        verbose_name = 'Configuração ABC de estoque'
        verbose_name_plural = 'Configurações ABC de estoque'

    def __str__(self):
        return f'{self.empresa} — classe {self.classe}'

    def clean(self):
        if self.multiplicador_minimo <= 0 or self.multiplicador_maximo <= 0:
            raise ValidationError('Os multiplicadores devem ser maiores que zero.')
