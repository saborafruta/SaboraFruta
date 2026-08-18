"""
Controle de qualidade — a inspeção antes de liberar.

O checklist é FIXO: os nove pontos da especificação, criados junto com a
inspeção. Fixo porque é o que faz duas inspeções serem comparáveis — se
cada inspetor escolhesse os itens, "80% de aprovação" numa semana e noutra
mediriam coisas diferentes, e o indicador deixaria de significar algo.

RETRABALHO NÃO É PERDA. A peça reprovada vira refugo; a que vai para
retrabalho volta para a costura e ainda pode ser vendida. Somá-las na mesma
coluna inflaria a perda da fábrica com peças que foram recuperadas — por
isso são três status e não dois, e só o reprovado vira perda no fluxo.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialManager, FilialScopedModel


class Inspecao(FilialScopedModel):
    """Uma inspeção de qualidade sobre uma ordem de produção."""

    class Status(models.TextChoices):
        EM_ANDAMENTO = 'em_andamento', 'Em andamento'
        APROVADO = 'aprovado', 'Aprovado'
        REPROVADO = 'reprovado', 'Reprovado'
        RETRABALHO = 'retrabalho', 'Retrabalho'

    # Status que exigem justificativa: sem ela o relatório de qualidade vira
    # uma lista de reprovações sem causa, e ninguém corrige o que não sabe.
    STATUS_COM_MOTIVO = (Status.REPROVADO, Status.RETRABALHO)

    numero = models.PositiveIntegerField(db_index=True)

    ordem = models.ForeignKey(
        'moda.OrdemProducao', on_delete=models.PROTECT, related_name='inspecoes',
    )
    data = models.DateField(default=timezone.localdate)
    inspetor = models.CharField(max_length=80, blank=True)

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.EM_ANDAMENTO, db_index=True,
    )

    quantidade_inspecionada = models.PositiveIntegerField(default=0)
    quantidade_aprovada = models.PositiveIntegerField(default=0)
    quantidade_reprovada = models.PositiveIntegerField(
        default=0, help_text='Refugo: peça que não volta.',
    )
    quantidade_retrabalho = models.PositiveIntegerField(
        default=0, help_text='Peça que volta para a produção e ainda pode ser vendida.',
    )

    motivo = models.TextField(
        blank=True,
        help_text='Obrigatório em reprovação e retrabalho — é o que permite corrigir a causa.',
    )
    observacao = models.TextField(blank=True)

    aplicada_no_fluxo = models.BooleanField(
        default=False, editable=False,
        help_text='Se os números já foram levados para a etapa de Qualidade da ordem.',
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='inspecoes_moda',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_inspecoes'
        ordering = ['-data', '-numero']
        unique_together = [('filial', 'numero')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['ordem']),
        ]
        verbose_name = 'Inspeção de qualidade'
        verbose_name_plural = 'Inspeções de qualidade'

    def __str__(self):
        return f'Inspeção #{self.numero:04d} — {self.ordem.numero}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = (
                Inspecao.all_objects
                .filter(filial_id=self.filial_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    # ── Checklist ────────────────────────────────────────────────────────

    @property
    def nao_conformidades(self) -> list:
        return [i for i in self.itens.all() if i.resultado == ItemInspecao.Resultado.NAO_CONFORME]

    @property
    def conforme(self) -> bool:
        """Nenhum ponto do checklist reprovado."""
        return not self.nao_conformidades

    @property
    def avaliados(self) -> int:
        return sum(
            1 for i in self.itens.all()
            if i.resultado != ItemInspecao.Resultado.PENDENTE
        )

    @property
    def completo(self) -> bool:
        """Todos os pontos do checklist foram olhados."""
        itens = list(self.itens.all())
        return bool(itens) and self.avaliados == len(itens)

    # ── Quantidades ──────────────────────────────────────────────────────

    @property
    def total_apontado(self) -> int:
        return (
            self.quantidade_aprovada + self.quantidade_reprovada
            + self.quantidade_retrabalho
        )

    @property
    def saldo(self) -> int:
        """Inspecionado − (aprovado + reprovado + retrabalho)."""
        return self.quantidade_inspecionada - self.total_apontado

    @property
    def fecha(self) -> bool:
        return self.saldo == 0

    @property
    def percentual_aprovacao(self) -> Decimal:
        if not self.quantidade_inspecionada:
            return Decimal('0')
        return (
            Decimal(self.quantidade_aprovada) / self.quantidade_inspecionada * 100
        ).quantize(Decimal('0.1'))

    @property
    def percentual_refugo(self) -> Decimal:
        """
        Só o reprovado. Retrabalho fica de fora de propósito: a peça voltou
        para a linha e ainda pode ser vendida, e contá-la como refugo faria a
        fábrica parecer pior do que é.
        """
        if not self.quantidade_inspecionada:
            return Decimal('0')
        return (
            Decimal(self.quantidade_reprovada) / self.quantidade_inspecionada * 100
        ).quantize(Decimal('0.1'))

    @property
    def encerrada(self) -> bool:
        return self.status != self.Status.EM_ANDAMENTO


class ItemInspecao(models.Model):
    """Um ponto do checklist."""

    class Ponto(models.TextChoices):
        # A ordem é a da especificação, e é a ordem em que se confere a peça:
        # do que se vê de longe (tamanho, cor) ao que se vê no detalhe.
        TAMANHO = 'tamanho', 'Tamanho correto'
        COR = 'cor', 'Cor correta'
        COSTURA = 'costura', 'Costura correta'
        ESTAMPA = 'estampa', 'Estampa correta'
        NUMERO = 'numero', 'Número correto'
        NOME = 'nome', 'Nome correto'
        ACABAMENTO = 'acabamento', 'Acabamento'
        LIMPEZA = 'limpeza', 'Limpeza'
        EMBALAGEM = 'embalagem', 'Embalagem'

    class Resultado(models.TextChoices):
        PENDENTE = 'pendente', 'Não avaliado'
        CONFORME = 'conforme', 'Conforme'
        NAO_CONFORME = 'nao_conforme', 'Não conforme'
        # Peça lisa não tem estampa, peça sem personalização não tem nome nem
        # número. Marcar "conforme" no que não existe mentiria no indicador.
        NAO_APLICA = 'nao_aplica', 'Não se aplica'

    inspecao = models.ForeignKey(
        Inspecao, on_delete=models.CASCADE, related_name='itens',
    )
    ponto = models.CharField(max_length=15, choices=Ponto.choices)
    ordem_exibicao = models.PositiveSmallIntegerField(default=0)

    resultado = models.CharField(
        max_length=15, choices=Resultado.choices, default=Resultado.PENDENTE,
    )
    observacao = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'moda_inspecoes_itens'
        ordering = ['ordem_exibicao']
        unique_together = [('inspecao', 'ponto')]
        verbose_name = 'Ponto do checklist'
        verbose_name_plural = 'Pontos do checklist'

    def __str__(self):
        return f'{self.get_ponto_display()}: {self.get_resultado_display()}'

    @property
    def reprovado(self) -> bool:
        return self.resultado == self.Resultado.NAO_CONFORME
