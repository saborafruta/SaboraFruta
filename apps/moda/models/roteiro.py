"""
Operações e roteiros de produção.

Duas coisas diferentes, de propósito separadas:

  1. `Operacao` é o CATÁLOGO da fábrica — Corte, Costura, Sublimação. Cada
     uma existe uma vez só, com o setor, a máquina, o tempo padrão e o
     custo da casa. É o cadastro que o menu de Engenharia pede.
  2. `Roteiro` é o CAMINHO DE UM PRODUTO por essas operações, na ordem que
     aquele produto exige. Uma camisa sublimada passa por Sublimação; uma
     bordada, por Bordado. É isso que "roteiros diferentes por produto"
     significa.

A linha do roteiro (`OperacaoRoteiro`) pode SOBRESCREVER tempo, custo,
máquina e responsável da operação. Em branco, herda do catálogo. A herança
é por leitura, e não cópia na gravação: corrigir o tempo padrão do Corte
deve corrigir a estimativa de todos os produtos que não pediram exceção --
o roteiro é um plano, não um registro histórico. (No pedido de produção é o
contrário, e por isso lá gola e manga são copiadas: aquilo é o que foi
combinado com o cliente e não pode mudar depois.)
"""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import ActiveModel, FilialManager, FilialScopedModel

MINUTOS_POR_HORA = Decimal('60')


class Operacao(FilialScopedModel, ActiveModel):
    """Uma operação do chão de fábrica, com o padrão da casa."""

    class Setor(models.TextChoices):
        # A ordem é a do fluxo produtivo, não alfabética: é assim que o
        # select da tela fica utilizável para quem preenche de cima a baixo.
        MODELAGEM = 'modelagem', 'Modelagem'
        CORTE = 'corte', 'Corte'
        ESTAMPARIA = 'estamparia', 'Estamparia'
        COSTURA = 'costura', 'Costura'
        ACABAMENTO = 'acabamento', 'Acabamento'
        QUALIDADE = 'qualidade', 'Qualidade'
        EXPEDICAO = 'expedicao', 'Expedição'

    class TipoCusto(models.TextChoices):
        POR_HORA = 'hora', 'Por hora'
        POR_PECA = 'peca', 'Por peça'

    nome = models.CharField(max_length=80)
    observacao = models.TextField(blank=True)

    # Ordem natural desta operação no fluxo da fábrica. É só o padrão: o
    # roteiro de cada produto tem a sequência dele, que manda.
    sequencia = models.PositiveSmallIntegerField(
        default=0,
        help_text='Posição no fluxo da fábrica. Serve de sugestão ao montar um roteiro.',
    )
    setor = models.CharField(max_length=20, choices=Setor.choices, default=Setor.COSTURA)
    maquina = models.CharField(
        max_length=80, blank=True,
        help_text='Ex.: Reta, Overloque, Galoneira, Calandra, Bordadeira.',
    )
    responsavel = models.CharField(
        max_length=80, blank=True,
        help_text='Quem responde pela operação — pessoa ou equipe.',
    )

    tempo_padrao = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('0'),
        verbose_name='Tempo padrão (min/peça)',
        help_text='Minutos que UMA peça leva nesta operação.',
        validators=[MinValueValidator(Decimal('0'))],
    )

    # Custo por hora e custo por peça convivem porque a confecção usa os
    # dois: costura interna se mede em hora de máquina, facção se paga por
    # peça. Guardar só um obrigaria a converter na cabeça e errar.
    tipo_custo = models.CharField(
        max_length=6, choices=TipoCusto.choices, default=TipoCusto.POR_HORA,
    )
    custo = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal('0'),
        help_text='Valor na unidade escolhida em "Tipo de custo".',
        validators=[MinValueValidator(Decimal('0'))],
    )

    capacidade = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
        verbose_name='Capacidade (peças/hora)',
        help_text='Quanto o SETOR entrega por hora, somando máquinas e pessoas.',
        validators=[MinValueValidator(Decimal('0'))],
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_operacoes'
        ordering = ['sequencia', 'nome']
        unique_together = [('filial', 'nome')]
        indexes = [models.Index(fields=['filial', 'setor'])]
        verbose_name = 'Operação'
        verbose_name_plural = 'Operações'

    def __str__(self):
        return self.nome

    @property
    def custo_por_peca(self) -> Decimal:
        """
        Custo desta operação em UMA peça.

        Com custo por hora, depende do tempo padrão: 30 min a R$ 18/h custam
        R$ 9. Com custo por peça, o valor já é o que se procura.
        """
        return custo_por_peca(self.tipo_custo, self.custo, self.tempo_padrao)


def custo_por_peca(tipo_custo, custo, tempo_minutos) -> Decimal:
    """
    Converte custo/hora em custo/peça usando o tempo, ou devolve o custo
    por peça como está.

    Função solta, e não método, porque a linha do roteiro precisa da mesma
    conta com os valores dela (que podem sobrescrever os da operação) --
    duplicar a fórmula nos dois lugares é como ela passa a divergir.
    """
    custo = custo or Decimal('0')
    if tipo_custo == Operacao.TipoCusto.POR_PECA:
        return custo.quantize(Decimal('0.01'))
    minutos = tempo_minutos or Decimal('0')
    return (custo * minutos / MINUTOS_POR_HORA).quantize(Decimal('0.01'))


class Roteiro(FilialScopedModel):
    """O caminho de um produto pela fábrica."""

    produto = models.OneToOneField(
        'moda.ProdutoModa', on_delete=models.CASCADE, related_name='roteiro',
    )
    versao = models.PositiveSmallIntegerField(default=1)
    observacoes = models.TextField(blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_roteiros'
        ordering = ['produto__codigo']
        verbose_name = 'Roteiro de produção'
        verbose_name_plural = 'Roteiros de produção'

    def __str__(self):
        return f'Roteiro {self.produto.codigo} v{self.versao}'

    # ── Totais ───────────────────────────────────────────────────────────

    @property
    def tempo_total(self) -> Decimal:
        """Minutos para produzir uma peça, somando todas as etapas."""
        return sum(
            (e.tempo for e in self.etapas.all()), Decimal('0'),
        ).quantize(Decimal('0.01'))

    @property
    def custo_total(self) -> Decimal:
        """Mão de obra de uma peça."""
        return sum(
            (e.custo_peca for e in self.etapas.all()), Decimal('0'),
        ).quantize(Decimal('0.01'))

    @property
    def gargalo(self):
        """
        A etapa de menor capacidade — a que limita a produção inteira.

        Etapa sem capacidade informada fica de fora: zero ali significa "não
        preenchido", e tratá-lo como capacidade zero apontaria o gargalo
        errado justamente onde falta informação.
        """
        candidatas = [e for e in self.etapas.all() if e.capacidade_efetiva > 0]
        return min(candidatas, key=lambda e: e.capacidade_efetiva, default=None)

    @property
    def capacidade_diaria(self) -> Decimal:
        """
        Peças por dia, pelo gargalo, numa jornada de 8 horas.

        Pelo gargalo e não pela média: a fábrica não entrega mais do que a
        etapa mais lenta deixa passar, e uma média daria um número bonito
        que a produção nunca alcança.
        """
        gargalo = self.gargalo
        if gargalo is None:
            return Decimal('0')
        return (gargalo.capacidade_efetiva * 8).quantize(Decimal('0.01'))


class OperacaoRoteiro(models.Model):
    """Uma etapa do roteiro: a operação, na posição dela, com exceções."""

    roteiro = models.ForeignKey(
        Roteiro, on_delete=models.CASCADE, related_name='etapas',
    )
    operacao = models.ForeignKey(
        Operacao, on_delete=models.PROTECT, related_name='etapas',
    )

    sequencia = models.PositiveSmallIntegerField(
        default=0, help_text='Ordem desta etapa NESTE produto.',
    )

    # Todos nulos = herda da operação. Nulo e não zero: zero é um valor
    # legítimo ("esta etapa não custa nada aqui") e precisa ser distinguível
    # de "não informei, use o padrão".
    tempo_padrao = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        verbose_name='Tempo (min/peça)',
        help_text='Em branco, usa o tempo padrão da operação.',
        validators=[MinValueValidator(Decimal('0'))],
    )
    custo = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text='Em branco, usa o custo da operação.',
        validators=[MinValueValidator(Decimal('0'))],
    )
    capacidade = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='Capacidade (peças/hora)',
        help_text='Em branco, usa a capacidade da operação.',
        validators=[MinValueValidator(Decimal('0'))],
    )
    maquina = models.CharField(max_length=80, blank=True)
    responsavel = models.CharField(max_length=80, blank=True)

    observacao = models.CharField(max_length=160, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_roteiro_operacoes'
        ordering = ['sequencia', 'id']
        unique_together = [('roteiro', 'operacao')]
        verbose_name = 'Etapa do roteiro'
        verbose_name_plural = 'Etapas do roteiro'

    def __str__(self):
        return f'{self.sequencia}. {self.operacao.nome}'

    # ── Valores efetivos (o desta etapa, ou o do catálogo) ────────────────

    @property
    def tempo(self) -> Decimal:
        if self.tempo_padrao is not None:
            return self.tempo_padrao
        return self.operacao.tempo_padrao or Decimal('0')

    @property
    def custo_valor(self) -> Decimal:
        if self.custo is not None:
            return self.custo
        return self.operacao.custo or Decimal('0')

    @property
    def capacidade_efetiva(self) -> Decimal:
        if self.capacidade is not None:
            return self.capacidade
        return self.operacao.capacidade or Decimal('0')

    @property
    def custo_peca(self) -> Decimal:
        """Custo desta etapa em uma peça, já resolvida a herança."""
        return custo_por_peca(self.operacao.tipo_custo, self.custo_valor, self.tempo)

    @property
    def maquina_efetiva(self) -> str:
        return self.maquina or self.operacao.maquina

    @property
    def responsavel_efetivo(self) -> str:
        return self.responsavel or self.operacao.responsavel

    @property
    def herda_tempo(self) -> bool:
        """Para a tela marcar o que veio do catálogo em vez do roteiro."""
        return self.tempo_padrao is None
