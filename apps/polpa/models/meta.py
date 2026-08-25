"""
A meta do dia — o número contra o qual a produção é lida.

SEM META, "8.500 kg" NÃO DIZ NADA. É muito? É pouco? A mesma produção é
comemorada num dia e cobrada no outro, dependendo de quem está olhando — e é
essa ambiguidade que faz o painel de chão de fábrica virar enfeite. Com a
meta, o número vira pergunta respondida: 85% do combinado.

DUAS METAS, UMA TABELA. A `data` nula é a META PADRÃO, que vale todo dia sem
meta própria; a `data` preenchida é a meta daquele dia específico — sexta de
feriado, dia de manutenção, pico de safra. Sem a distinção, a fábrica teria
de cadastrar 365 metas iguais para poder mudar uma.

A META É EM QUILO, e não em unidade. É em quilo que a fruta entra, que a
câmara enche e que o custo é comparado entre produtos — e uma meta em
unidades misturaria pote de 100 g com balde de 10 kg no mesmo total.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class MetaProducao(FilialScopedModel):
    """Quanto a fábrica se compromete a produzir num dia."""

    data = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='Vazio = meta padrão, que vale todo dia sem meta própria.',
    )
    meta_kg = models.DecimalField(
        max_digits=12, decimal_places=3,
        validators=[MinValueValidator(0)],
        help_text='Quilos de produto acabado esperados no dia.',
    )
    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_metas_producao'
        ordering = ['-data']
        constraints = [
            # UMA META POR DIA, e uma padrão só. Duas metas para o mesmo dia
            # dariam dois percentuais de atingimento para a mesma produção —
            # e a fábrica escolheria o que lhe convém.
            models.UniqueConstraint(
                fields=['filial', 'data'], name='polpa_meta_por_dia',
                condition=models.Q(data__isnull=False),
            ),
            models.UniqueConstraint(
                fields=['filial'], name='polpa_meta_padrao_unica',
                condition=models.Q(data__isnull=True),
            ),
        ]
        verbose_name = 'Meta de produção'
        verbose_name_plural = 'Metas de produção'

    def __str__(self):
        if self.data:
            return f'{self.data:%d/%m/%Y}: {self.meta_kg} kg'
        return f'Meta padrão: {self.meta_kg} kg'

    @property
    def e_padrao(self) -> bool:
        return self.data is None

    @classmethod
    def do_dia(cls, filial, dia):
        """
        A meta que vale para este dia: a específica, ou a padrão.

        `None` quando não há nenhuma — e a tela diz isso em vez de assumir
        zero. Meta zero significaria "não é para produzir nada hoje", e
        qualquer produção apareceria como atingimento infinito.
        """
        especifica = cls.objects.for_filial(filial).filter(data=dia).first()
        if especifica:
            return especifica
        return cls.objects.for_filial(filial).filter(data__isnull=True).first()
