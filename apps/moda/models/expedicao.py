"""
Expedição — da produção concluída até a entrega confirmada.

O processo tem seis etapas e elas são LINEARES: não se separa o que não foi
conferido, nem se despacha o que não foi embalado. A ordem existe porque
cada etapa depende do resultado da anterior, e um documento que pula etapas
chega ao cliente com volume faltando e ninguém sabendo onde.

O CÓDIGO DE LEITURA é um token curto e opaco, não o número sequencial. O
número serve para as pessoas conversarem ("expedição 42"); o código serve
para o leitor. Usar o sequencial no código de barras faria um dígito errado
na digitação encontrar OUTRA expedição — com token, erra e não encontra
nada, que é o comportamento seguro.
"""
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialManager, FilialScopedModel


def _token() -> str:
    """Token curto o bastante para caber num código de barras legível."""
    return secrets.token_urlsafe(9)


class Expedicao(FilialScopedModel):
    """O documento que acompanha a ordem da fábrica até o cliente."""

    class Status(models.TextChoices):
        # A ordem aqui É o processo. `Status.choices` alimenta a régua da
        # tela e a validação de avanço — trocar a ordem troca o processo.
        PRODUCAO_CONCLUIDA = 'producao', 'Produção Concluída'
        CONFERENCIA = 'conferencia', 'Conferência'
        SEPARACAO = 'separacao', 'Separação'
        EMBALAGEM = 'embalagem', 'Embalagem'
        DESPACHO = 'despacho', 'Despacho'
        ENTREGA = 'entrega', 'Entrega'
        CANCELADA = 'cancelada', 'Cancelada'

    ETAPAS = (
        Status.PRODUCAO_CONCLUIDA, Status.CONFERENCIA, Status.SEPARACAO,
        Status.EMBALAGEM, Status.DESPACHO, Status.ENTREGA,
    )

    numero = models.PositiveIntegerField(db_index=True)
    codigo = models.CharField(
        max_length=16, unique=True, editable=False, db_index=True,
        help_text='Token do código de barras/QR. Diferente do número, de propósito.',
    )

    ordem = models.ForeignKey(
        'moda.OrdemProducao', on_delete=models.PROTECT, related_name='expedicoes',
    )
    status = models.CharField(
        max_length=15, choices=Status.choices,
        default=Status.PRODUCAO_CONCLUIDA, db_index=True,
    )

    # Uma data por etapa: o histórico do documento é a própria linha do
    # tempo, e uma tabela de eventos à parte seria pesada demais para o que
    # se pergunta aqui ("quando saiu?").
    data_conferencia = models.DateTimeField(null=True, blank=True)
    data_separacao = models.DateTimeField(null=True, blank=True)
    data_embalagem = models.DateTimeField(null=True, blank=True)
    data_despacho = models.DateTimeField(null=True, blank=True)
    data_entrega = models.DateTimeField(null=True, blank=True)

    conferido_por = models.CharField(max_length=80, blank=True)
    transportadora = models.CharField(max_length=120, blank=True)
    rastreio = models.CharField(max_length=60, blank=True)
    recebido_por = models.CharField(
        max_length=120, blank=True, help_text='Quem assinou o recebimento.',
    )
    observacao = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='expedicoes_moda',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_expedicoes'
        ordering = ['-numero']
        unique_together = [('filial', 'numero')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['ordem']),
        ]
        verbose_name = 'Expedição'
        verbose_name_plural = 'Expedições'

    def __str__(self):
        return f'Expedição #{self.numero:04d} — {self.ordem.numero}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = _token()
        if not self.numero:
            ultimo = (
                Expedicao.all_objects
                .filter(filial_id=self.filial_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    # ── Leituras da ordem ────────────────────────────────────────────────

    @property
    def pedido(self):
        return self.ordem.pedido

    @property
    def cliente(self):
        return self.ordem.pedido.cliente

    @property
    def grade_esperada(self):
        """A grade do item da ordem — o que deveria estar na caixa."""
        return self.ordem.item.grade.select_related('tamanho').all()

    @property
    def quantidade_esperada(self) -> int:
        return self.ordem.quantidade

    # ── Conferência ──────────────────────────────────────────────────────

    @property
    def quantidade_conferida(self) -> int:
        return sum(i.quantidade for i in self.conferencia.all())

    @property
    def divergencia_conferencia(self) -> int:
        """Esperado − conferido. Positivo = falta peça na caixa."""
        return self.quantidade_esperada - self.quantidade_conferida

    @property
    def conferencia_fecha(self) -> bool:
        return self.divergencia_conferencia == 0

    @property
    def divergencias_por_tamanho(self) -> list[dict]:
        """
        Onde a conferência não bate, tamanho a tamanho.

        Por tamanho e não só no total porque o total pode fechar com dois
        erros que se cancelam — dois P a mais e dois G a menos somam zero e
        o cliente recebe a caixa errada.
        """
        conferido = {i.tamanho_id: i.quantidade for i in self.conferencia.all()}
        linhas = []
        for celula in self.grade_esperada:
            achado = conferido.get(celula.tamanho_id, 0)
            if achado != celula.quantidade:
                linhas.append({
                    'tamanho': celula.tamanho,
                    'esperado': celula.quantidade,
                    'conferido': achado,
                    'diferenca': achado - celula.quantidade,
                })
        return linhas

    # ── Volumes ──────────────────────────────────────────────────────────

    @property
    def total_volumes(self) -> int:
        return self.volumes.count()

    @property
    def pecas_nos_volumes(self) -> int:
        return sum(v.quantidade for v in self.volumes.all())

    @property
    def peso_total(self) -> Decimal:
        return sum((v.peso_kg or Decimal('0') for v in self.volumes.all()), Decimal('0'))

    @property
    def volumes_fecham(self) -> bool:
        """As peças nos volumes batem com o que foi conferido."""
        return self.pecas_nos_volumes == self.quantidade_conferida

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def cancelada(self) -> bool:
        return self.status == self.Status.CANCELADA

    @property
    def entregue(self) -> bool:
        return self.status == self.Status.ENTREGA

    @property
    def posicao(self) -> int:
        """Índice da etapa atual na régua. −1 quando cancelada."""
        try:
            return self.ETAPAS.index(self.status)
        except ValueError:
            return -1

    def passou_por(self, etapa: str) -> bool:
        """Para a régua da tela pintar o que já aconteceu."""
        if self.cancelada:
            return False
        try:
            return self.posicao >= self.ETAPAS.index(etapa)
        except ValueError:
            return False


class ItemConferencia(models.Model):
    """Quantas peças de cada tamanho foram efetivamente conferidas."""

    expedicao = models.ForeignKey(
        Expedicao, on_delete=models.CASCADE, related_name='conferencia',
    )
    tamanho = models.ForeignKey(
        'moda.Tamanho', on_delete=models.PROTECT, related_name='conferencias_moda',
    )
    quantidade = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'moda_expedicao_conferencia'
        ordering = ['tamanho__ordem', 'tamanho__sigla']
        unique_together = [('expedicao', 'tamanho')]
        verbose_name = 'Item conferido'
        verbose_name_plural = 'Itens conferidos'

    def __str__(self):
        return f'{self.tamanho.sigla}: {self.quantidade}'


class Volume(models.Model):
    """Uma caixa. Tem código próprio porque é ela que viaja."""

    expedicao = models.ForeignKey(
        Expedicao, on_delete=models.CASCADE, related_name='volumes',
    )
    numero = models.PositiveSmallIntegerField()
    codigo = models.CharField(max_length=16, unique=True, editable=False, db_index=True)

    quantidade = models.PositiveIntegerField(default=0)
    peso_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    observacao = models.CharField(max_length=160, blank=True)

    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'moda_expedicao_volumes'
        ordering = ['numero']
        unique_together = [('expedicao', 'numero')]
        verbose_name = 'Volume'
        verbose_name_plural = 'Volumes'

    def __str__(self):
        return f'Volume {self.numero}/{self.expedicao.total_volumes}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = _token()
        if not self.numero:
            ultimo = (
                Volume.objects
                .filter(expedicao_id=self.expedicao_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)


class ConferenciaPessoa(models.Model):
    """
    Uma peça PERSONALIZADA conferida antes de sair — a camisa do João, a do
    Pedro.

    Convive com `ItemConferencia` e não a substitui: aquela conta QUANTAS
    peças de cada tamanho saíram, e é o que fecha contra a ordem. Esta diz
    QUAIS pessoas foram atendidas. Num pedido de time, as duas perguntas são
    diferentes -- a contagem por tamanho fecha e ainda assim a camisa do
    Lucas pode ter ficado para trás, porque a peça dele é única.

    A LINHA EXISTIR É A CONFERÊNCIA. Desmarcar apaga o registro em vez de
    gravar `False`, e assim `conferido_em`/`conferido_por` nunca contam a
    história de uma conferência que foi desfeita.
    """

    expedicao = models.ForeignKey(
        Expedicao, on_delete=models.CASCADE, related_name='conferencia_pessoas',
    )
    individual = models.ForeignKey(
        'moda.PersonalizacaoIndividual', on_delete=models.CASCADE,
        related_name='conferencias',
    )
    conferido_em = models.DateTimeField(auto_now_add=True)
    conferido_por = models.CharField(max_length=80, blank=True)

    class Meta:
        db_table = 'moda_expedicao_conferencia_pessoa'
        ordering = ['individual__ordem', 'individual_id']
        unique_together = [('expedicao', 'individual')]
        verbose_name = 'Pessoa conferida'
        verbose_name_plural = 'Pessoas conferidas'

    def __str__(self):
        return f'{self.individual} conferido'
