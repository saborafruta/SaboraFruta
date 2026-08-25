"""
O que produz: linha e máquina, com a capacidade de cada uma.

POR QUE PRECISA EXISTIR. Planejar é encaixar produção em capacidade — e sem
saber quanto a despolpadeira faz por dia, "programar para terça" é um
palpite. A pergunta que o PCP responde ("cabe?") não tem resposta sem este
cadastro, e é por isso que ele vem antes do calendário.

LINHA E MÁQUINA NO MESMO MODELO, com um campo de tipo. São a mesma coisa
para o planejamento — um recurso com capacidade limitada e uma fila — e
separá-los em duas tabelas daria duas telas de cadastro, duas consultas de
carga e a primeira divergência no dia em que alguém somasse só uma delas.

A LINHA DO ERP É REAPROVEITADA quando existe (`produtos.LinhaProducao` já
guarda metas de rendimento e taxas de custo/hora). Aqui entra o que falta
para planejar: quanto sai por dia e quantas horas ela roda.
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel

ZERO = Decimal('0')


class Recurso(FilialScopedModel):
    """Uma linha ou máquina, com o quanto ela aguenta por dia."""

    class Tipo(models.TextChoices):
        LINHA = 'linha', 'Linha de produção'
        MAQUINA = 'maquina', 'Máquina'

    nome = models.CharField(max_length=80, db_index=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.LINHA)

    linha_producao = models.ForeignKey(
        'produtos.LinhaProducao', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='recursos_polpa',
        help_text='Liga à linha do ERP, quando ela já existe.',
    )

    # ── Capacidade ───────────────────────────────────────────────────────
    # Nula é "ninguém mediu". Zero seria um recurso que não produz nada, e o
    # planejamento acusaria estouro de capacidade em toda programação.
    capacidade_dia = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
        validators=[MinValueValidator(0)],
        help_text='Quanto este recurso produz num dia, na unidade do produto.',
    )
    horas_dia = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('8'),
        validators=[MinValueValidator(0)],
        help_text='Horas disponíveis por dia — o turno.',
    )
    setup_minutos = models.PositiveSmallIntegerField(
        default=0,
        help_text='Tempo de troca entre produções (higienização, troca de sabor).',
    )

    ativo = models.BooleanField(default=True)
    observacao = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_recursos'
        ordering = ['tipo', 'nome']
        unique_together = [('filial', 'nome')]
        indexes = [models.Index(fields=['filial', 'ativo'])]
        verbose_name = 'Recurso de produção'
        verbose_name_plural = 'Recursos de produção'

    def __str__(self):
        return self.nome

    @property
    def tem_capacidade(self) -> bool:
        """Se dá para planejar por ele. Sem isso, 'cabe?' não tem resposta."""
        return bool(self.capacidade_dia)

    def ocupacao(self, carga) -> Decimal | None:
        """
        Quanto por cento do dia esta carga ocupa.

        `None` sem capacidade cadastrada -- e a tela diz isso, em vez de
        mostrar 0% (que seria lido como recurso livre) ou 100% (que seria
        lido como lotado).
        """
        if not self.capacidade_dia:
            return None
        return (Decimal(carga) / self.capacidade_dia * 100).quantize(Decimal('0.1'))
