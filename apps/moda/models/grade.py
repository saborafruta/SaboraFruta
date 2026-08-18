"""
Tamanhos e grades.

Tamanho é cadastro próprio (e não texto solto no produto) porque a ordem
importa em toda tela do vertical: a grade da ficha de produção é lida
PP, P, M, G, GG, XGG — nunca em ordem alfabética, que daria
G, GG, M, P, PP, XGG. Por isso `ordem` é campo, não algo derivado do nome.
"""
from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel


class Tamanho(FilialScopedModel, ActiveModel):
    """Um tamanho isolado. Reaproveitado entre grades."""

    class Tipo(models.TextChoices):
        ADULTO = 'adulto', 'Adulto'
        PLUS_SIZE = 'plus_size', 'Plus Size'
        INFANTIL = 'infantil', 'Infantil'
        UNICO = 'unico', 'Único'
        OUTRO = 'outro', 'Outro'

    sigla = models.CharField(
        max_length=6, help_text='Como aparece na grade e no SKU (PP, M, G1, 12).',
    )
    nome = models.CharField(max_length=40, blank=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.ADULTO)
    # Ordem de exibição dentro do tipo. Espaçada de 10 em 10 no seed para
    # caber um tamanho novo no meio sem renumerar os existentes.
    ordem = models.PositiveIntegerField(default=0)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_tamanhos'
        ordering = ['tipo', 'ordem', 'sigla']
        unique_together = [('filial', 'sigla')]
        verbose_name = 'Tamanho'
        verbose_name_plural = 'Tamanhos'

    def __str__(self):
        return self.sigla

    def save(self, *args, **kwargs):
        self.sigla = (self.sigla or '').strip().upper()
        super().save(*args, **kwargs)


class Grade(FilialScopedModel, ActiveModel):
    """
    Conjunto ordenado de tamanhos (Adulto, Plus Size, Infantil ou uma
    personalizada). É o que o produto referencia para saber quais variantes
    gerar.
    """

    nome = models.CharField(max_length=60)
    tipo = models.CharField(
        max_length=20, choices=Tamanho.Tipo.choices, default=Tamanho.Tipo.ADULTO,
    )
    descricao = models.CharField(max_length=160, blank=True)
    # Marca as três grades da especificação, criadas pelo seed. Serve para
    # a tela avisar antes de alterar uma grade que outros produtos usam.
    padrao = models.BooleanField(
        default=False,
        help_text='Grade padrão do sistema (Adulto, Plus Size, Infantil).',
    )
    tamanhos = models.ManyToManyField(
        Tamanho, through='ItemGrade', related_name='grades',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_grades'
        ordering = ['nome']
        unique_together = [('filial', 'nome')]
        verbose_name = 'Grade'
        verbose_name_plural = 'Grades'

    def __str__(self):
        return self.nome

    def tamanhos_ordenados(self):
        """Tamanhos na ordem da grade — a que vai para a ficha de produção."""
        return [i.tamanho for i in self.itens.select_related('tamanho').all()]

    @property
    def resumo(self) -> str:
        """Ex.: 'PP | P | M | G | GG | XGG', para listar sem abrir a grade."""
        return ' | '.join(t.sigla for t in self.tamanhos_ordenados())


class ItemGrade(models.Model):
    """
    Tamanho dentro de uma grade, com ordem própria.

    A ordem fica aqui, e não só no Tamanho, porque a mesma sigla pode
    ocupar posições diferentes em grades diferentes -- uma grade
    personalizada pode listar só G, GG, XGG e começar do zero.
    """

    grade = models.ForeignKey(Grade, on_delete=models.CASCADE, related_name='itens')
    tamanho = models.ForeignKey(Tamanho, on_delete=models.PROTECT, related_name='itens_grade')
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_itens_grade'
        ordering = ['ordem', 'id']
        unique_together = [('grade', 'tamanho')]
        verbose_name = 'Tamanho da grade'
        verbose_name_plural = 'Tamanhos da grade'

    def __str__(self):
        return f'{self.grade.nome} — {self.tamanho.sigla}'
