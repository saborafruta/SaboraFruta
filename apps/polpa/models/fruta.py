"""
A fruta, como a fábrica de polpa a conhece.

POR QUE NÃO BASTA O `produtos.Produto`. No catálogo do ERP a manga é um
item com preço e unidade. Aqui ela precisa responder três perguntas que só
existem nesta indústria:

  · a carga PODE entrar? (Brix mínimo, pH máximo — fruta verde não vira
    polpa boa, e aceitar por não ter a régua na tela é prejuízo que só
    aparece semanas depois, no sabor);
  · quanto ela DEVE render? (100 kg de manga não viram 100 kg de polpa:
    casca e caroço saem. Sem o esperado, o rendimento real não tem contra
    o que ser comparado, e perda vira "normal");
  · estamos na SAFRA? (fora dela o preço muda e a qualidade cai, e quem
    compra precisa ver isso na hora, não no fechamento do mês).

Por isso `Fruta` é uma FICHA que aponta para o produto do ERP, e não um
segundo cadastro de produto. O estoque, o custo e a nota continuam sendo do
`Produto`; aqui mora só o que é da fruta.
"""
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

MESES = [
    (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
    (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
    (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
]


class Fruta(FilialScopedModel):
    """A matéria-prima in natura, com a régua que decide aceitá-la."""

    nome = models.CharField(max_length=80, db_index=True)
    variedade = models.CharField(
        max_length=60, blank=True,
        help_text='Tommy, Palmer, Espada… muda o rendimento e o Brix.',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='frutas_polpa',
        help_text='Item do catálogo que representa esta fruta in natura.',
    )

    # ── A régua de aceitação ─────────────────────────────────────────────
    # Nulo é "não exijo", e não zero: zero seria uma exigência que toda
    # carga cumpre, e esconderia que ninguém definiu a régua ainda.
    brix_minimo = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Sólidos solúveis mínimos. Abaixo disso a fruta está verde.',
    )
    ph_maximo = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Acima disso a acidez não protege o produto.',
    )
    impureza_maxima = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='% de folha, galho e terra tolerada na carga.',
    )

    # ── O que ela deve render ────────────────────────────────────────────
    rendimento_esperado = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='% de polpa que sai da fruta limpa. Ex.: manga ≈ 60%.',
    )

    # ── Safra ────────────────────────────────────────────────────────────
    safra_inicio = models.PositiveSmallIntegerField(
        choices=MESES, null=True, blank=True,
    )
    safra_fim = models.PositiveSmallIntegerField(
        choices=MESES, null=True, blank=True,
    )

    ativo = models.BooleanField(default=True)
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_frutas'
        ordering = ['nome', 'variedade']
        unique_together = [('filial', 'nome', 'variedade')]
        indexes = [models.Index(fields=['filial', 'ativo'])]
        verbose_name = 'Fruta'
        verbose_name_plural = 'Frutas'

    def __str__(self):
        return f'{self.nome} {self.variedade}'.strip()

    # ── Leituras ─────────────────────────────────────────────────────────

    @property
    def tem_regua(self) -> bool:
        """Se alguém já definiu o que aceitar. Sem isso não há reprovação."""
        return any((self.brix_minimo, self.ph_maximo, self.impureza_maxima))

    def na_safra(self, mes: int) -> bool:
        """
        Se o mês cai na safra. Safra que VIRA O ANO (novembro a fevereiro)
        é o caso normal em fruta tropical, e um `inicio <= mes <= fim`
        ingênuo diria que dezembro está fora.
        """
        if not self.safra_inicio or not self.safra_fim:
            return True
        if self.safra_inicio <= self.safra_fim:
            return self.safra_inicio <= mes <= self.safra_fim
        return mes >= self.safra_inicio or mes <= self.safra_fim
