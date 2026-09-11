"""
Peso por tamanho, de um tecido, num tipo de peça -- independente de
qualquer produto do catálogo.

"Malha PV a camisa P - 145g, M - 176g..." é uma tabela que vale para
QUALQUER camisa cortada em PV, não uma característica de um SKU. Prender
o peso a uma ficha técnica (que é 1 por produto) obrigaria a recadastrar
o mesmo peso a cada variante ou a criar produto só para guardar peso --
os dois exatamente o que se queria evitar.

`grade` entra na chave pelo mesmo motivo de `PesoTamanhoFicha`: a mesma
sigla de tamanho (ex.: G) pode pesar diferente numa grade Oversized do
que numa Adulto, e `Tamanho` é único por filial -- sem a grade na chave,
a segunda gravação sobrescreveria a primeira.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class PesoTecidoGrade(FilialScopedModel):
    """O peso de UM tamanho, de UMA grade, para um tecido e tipo de peça."""

    tecido = models.ForeignKey(
        'moda.Tecido', on_delete=models.PROTECT, related_name='pesos_grade',
    )
    # Texto, e não FK: o "tipo de peça" da OP 2.0 também é texto (a label
    # escolhida na Estrutura da peça, ver `services/op2_estrutura.py`) --
    # amarrar isto a um cadastro novo exigiria manter os dois em sincronia.
    tipo_peca = models.CharField(
        max_length=80,
        help_text='O mesmo "Tipo de peça" escolhido na OP 2.0 (ex.: Camisa, Camisa Polo).',
    )
    grade = models.ForeignKey(
        'moda.Grade', on_delete=models.PROTECT, related_name='+',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='+',
    )
    peso_g = models.DecimalField(
        max_digits=8, decimal_places=1, null=True, blank=True,
        verbose_name='Peso (g)',
        validators=[MinValueValidator(Decimal('0'))],
    )
    # Copiada do `ItemGrade.ordem` no momento em que a grade é acrescentada
    # -- a tabela mantém a ordem PP, P, M... mesmo que a grade original
    # seja reordenada depois.
    ordem = models.PositiveIntegerField(default=0)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_pesos_tecido_grade'
        ordering = ['tecido__nome', 'tipo_peca', 'grade__nome', 'ordem']
        unique_together = [('filial', 'tecido', 'tipo_peca', 'grade', 'tamanho')]
        indexes = [
            models.Index(fields=['filial', 'tecido', 'tipo_peca']),
        ]
        verbose_name = 'Peso por tecido e grade'
        verbose_name_plural = 'Pesos por tecido e grade'

    def __str__(self):
        peso = f'{self.peso_g} g' if self.peso_g is not None else '—'
        return f'{self.tecido.nome} · {self.tipo_peca} · {self.grade.nome} · {self.tamanho.sigla}: {peso}'
