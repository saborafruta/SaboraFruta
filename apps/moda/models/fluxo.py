"""
Fluxo de produção — as etapas por onde a ordem caminha.

Onze etapas fixas, do pedido à entrega. FIXAS de propósito, e não derivadas
do roteiro: o roteiro é engenharia (quinze operações, com tempo e custo por
peça), e este fluxo é acompanhamento (onde a ordem está agora). São
granularidades diferentes, e amarrar uma na outra faria a tela de
acompanhamento mudar de forma a cada produto — o encarregado que olha o
painel de manhã veria um desenho diferente para cada OP.

Cada etapa é criada junto com a ordem, já pendente. Criar sob demanda daria
um fluxo com buracos: quem abre a OP no dia seguinte à emissão veria só as
etapas que alguém tocou, e "não iniciada" ficaria indistinguível de
"não existe".

A QUANTIDADE PLANEJADA DE CADA ETAPA HERDA da etapa anterior: se o corte
planejou 40 e produziu 39, a costura planeja 39, não 40. Sem essa cadeia, a
perda desapareceria do plano e todas as etapas seguintes ficariam
planejando um número que já não existe no chão.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class EtapaOrdem(models.Model):
    """Uma etapa do fluxo de uma ordem de produção."""

    class Etapa(models.TextChoices):
        # A ordem aqui É o fluxo. `Etapa.choices` alimenta a criação das
        # etapas e a tela; trocar a ordem aqui troca o fluxo inteiro.
        PEDIDO = 'pedido', 'Pedido'
        PLANEJAMENTO = 'planejamento', 'Planejamento'
        MATERIAIS = 'materiais', 'Materiais'
        CORTE = 'corte', 'Corte'
        ESTAMPA = 'estampa', 'Sublimação / Bordado / Silk'
        COSTURA = 'costura', 'Costura'
        ACABAMENTO = 'acabamento', 'Acabamento'
        QUALIDADE = 'qualidade', 'Qualidade'
        EMBALAGEM = 'embalagem', 'Embalagem'
        EXPEDICAO = 'expedicao', 'Expedição'
        ENTREGA = 'entrega', 'Entrega'

    class Status(models.TextChoices):
        PENDENTE = 'pendente', 'Pendente'
        EM_ANDAMENTO = 'em_andamento', 'Em andamento'
        CONCLUIDA = 'concluida', 'Concluída'
        # Não toda peça passa por estampa, e forçar "concluída" numa etapa
        # que não aconteceu mentiria no relatório de produção. Pulada diz a
        # verdade: esta ordem não passa por aqui.
        PULADA = 'pulada', 'Não se aplica'

    ordem = models.ForeignKey(
        'moda.OrdemProducao', on_delete=models.CASCADE, related_name='etapas',
    )
    etapa = models.CharField(max_length=15, choices=Etapa.choices)
    sequencia = models.PositiveSmallIntegerField()

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDENTE, db_index=True,
    )
    responsavel = models.CharField(
        max_length=80, blank=True, help_text='Quem responde por esta etapa.',
    )

    data_inicio = models.DateField(null=True, blank=True, verbose_name='Data de início')
    data_prevista = models.DateField(null=True, blank=True)
    data_conclusao = models.DateField(null=True, blank=True, verbose_name='Data concluída')

    # Nula = herda da etapa anterior. Nula e não zero: zero é um valor
    # legítimo (nada a fazer aqui) e precisa ser distinguível de "use o que
    # veio da etapa de trás".
    quantidade_planejada = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Em branco, herda o produzido da etapa anterior.',
    )
    quantidade_produzida = models.PositiveIntegerField(default=0)
    perda = models.PositiveIntegerField(
        default=0, help_text='Peças perdidas nesta etapa: refugo, corte errado, defeito.',
    )

    observacao = models.TextField(blank=True)

    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='etapas_moda',
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'moda_etapas_ordem'
        ordering = ['sequencia']
        unique_together = [('ordem', 'etapa')]
        indexes = [models.Index(fields=['ordem', 'status'])]
        verbose_name = 'Etapa da ordem'
        verbose_name_plural = 'Etapas da ordem'

    def __str__(self):
        return f'{self.ordem_id} — {self.get_etapa_display()}'

    # ── Quantidades ──────────────────────────────────────────────────────

    @property
    def planejada(self) -> int:
        """
        Quantidade planejada, resolvida a herança.

        Sem valor próprio, pega o produzido da última etapa concluída antes
        desta; não havendo nenhuma, a quantidade da ordem. É essa cadeia que
        faz a perda do corte chegar ao planejamento da costura.
        """
        if self.quantidade_planejada is not None:
            return self.quantidade_planejada

        anterior = self._anterior_concluida()
        if anterior is not None:
            return anterior.quantidade_produzida
        return self.ordem.quantidade

    def _anterior_concluida(self):
        """Última etapa concluída antes desta, ou None."""
        concluidas = [
            e for e in self.ordem.etapas.all()
            if e.sequencia < self.sequencia and e.status == self.Status.CONCLUIDA
        ]
        return max(concluidas, key=lambda e: e.sequencia, default=None)

    @property
    def saldo(self) -> int:
        """Planejado − produzido − perda. Positivo = ainda falta."""
        return self.planejada - self.quantidade_produzida - self.perda

    @property
    def fecha(self) -> bool:
        """Produzido + perda batem com o planejado."""
        return self.saldo == 0

    @property
    def percentual(self) -> Decimal:
        planejada = self.planejada
        if not planejada:
            return Decimal('0')
        return (Decimal(self.quantidade_produzida) / planejada * 100).quantize(Decimal('0.1'))

    @property
    def percentual_perda(self) -> Decimal:
        planejada = self.planejada
        if not planejada:
            return Decimal('0')
        return (Decimal(self.perda) / planejada * 100).quantize(Decimal('0.1'))

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def concluida(self) -> bool:
        return self.status == self.Status.CONCLUIDA

    @property
    def encerrada(self) -> bool:
        """Concluída ou pulada — nos dois casos a etapa não está mais aberta."""
        return self.status in (self.Status.CONCLUIDA, self.Status.PULADA)

    @property
    def atrasada(self) -> bool:
        """Passou da data prevista sem ter sido encerrada."""
        from django.utils import timezone
        if self.encerrada or not self.data_prevista:
            return False
        return self.data_prevista < timezone.localdate()

    @property
    def divergencia(self) -> str:
        """
        Aviso quando os números da etapa não fecham.

        Só para etapa concluída: durante a produção é natural que não feche
        ainda, e avisar ali viraria ruído que se aprende a ignorar — e aí o
        aviso que importa também passa batido.
        """
        if self.status != self.Status.CONCLUIDA or self.fecha:
            return ''
        if self.saldo > 0:
            return (
                f'Faltam {self.saldo} peça(s): planejado {self.planejada}, '
                f'produzido {self.quantidade_produzida}, perda {self.perda}.'
            )
        return (
            f'Sobram {abs(self.saldo)} peça(s): produzido {self.quantidade_produzida} '
            f'mais perda {self.perda} passa do planejado {self.planejada}.'
        )
