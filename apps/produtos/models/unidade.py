"""Unidades de medida com conversao."""
from django.db import models

from apps.core.models.base import FilialManager, TimestampedModel


class UnidadeMedidaManager(FilialManager):
    def for_filial(self, filial):
        if filial is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial=filial,
            filiais_vinculo__ativo=True,
        ).distinct()

    def for_empresa(self, empresa):
        if empresa is None:
            return self.get_queryset().none()
        return self.get_queryset().filter(
            filiais_vinculo__filial__empresa=empresa,
            filiais_vinculo__ativo=True,
        ).distinct()


class UnidadeMedida(models.Model):
    class Tipo(models.TextChoices):
        UNIDADE = 'unidade', 'Unidade'
        PESO = 'peso', 'Peso'
        VOLUME = 'volume', 'Volume'
        COMPRIMENTO = 'comprimento', 'Comprimento'
        AREA = 'area', 'Area'

    empresa = models.ForeignKey(
        'core.Empresa', on_delete=models.CASCADE, related_name='unidades_medida',
    )
    sigla = models.CharField(max_length=6)
    descricao = models.CharField(max_length=40)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, blank=True)
    fator_conversao_base = models.DecimalField(
        max_digits=14, decimal_places=6, default=1,
        help_text='Conversao para a unidade base da categoria (KG=1, G=0.001)',
    )
    casas_decimais = models.PositiveSmallIntegerField(
        default=3,
        help_text='Casas decimais aceitas na quantidade desta unidade ao arredondar '
                   '(ver apps.produtos.services.conversao). Unidades de contagem '
                   '(tipo=Unidade) normalmente usam 0.',
    )
    id_externo = models.CharField(max_length=100, blank=True, db_index=True)
    ativo = models.BooleanField(default=True)

    objects = UnidadeMedidaManager()

    class Meta:
        db_table = 'unidades_medida'
        ordering = ['sigla']
        unique_together = [('empresa', 'sigla')]
        verbose_name = 'Unidade de Medida'
        verbose_name_plural = 'Unidades de Medida'

    def __str__(self):
        return f'{self.sigla} ({self.descricao})'


class UnidadeMedidaFilial(TimestampedModel):
    unidade = models.ForeignKey(UnidadeMedida, on_delete=models.CASCADE, related_name='filiais_vinculo')
    filial = models.ForeignKey('core.Filial', on_delete=models.CASCADE, related_name='unidades_medida_vinculadas')
    ativo = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = 'unidades_medida_filiais'
        ordering = ['unidade', 'filial']
        unique_together = [('unidade', 'filial')]
        indexes = [
            models.Index(fields=['filial', 'ativo']),
            models.Index(fields=['unidade', 'ativo']),
        ]

    def __str__(self):
        return f'{self.unidade} - {self.filial}'
