"""
Pedido de produção — a ficha de produção da confecção, em forma digital.

É o documento que atravessa o vertical inteiro: nasce no comercial e vai
sendo carimbado por corte, estampa, costura, acabamento e expedição. Por
isso o status não é um campo cosmético — é o que diz em que etapa a peça
está agora.

Este arquivo cobre o cabeçalho do pedido. Os itens (produto × grade com
quantidade por tamanho) e as etapas de produção entram na sequência,
apontando para cá.
"""
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models.base import FilialManager, FilialScopedModel

from .qr import ComCodigoQr


class PedidoProducao(ComCodigoQr, FilialScopedModel):

    PREFIXO_QR = 'PED'

    class Status(models.TextChoices):
        # A ordem aqui é a ordem do fluxo, não alfabética: `Status.choices`
        # alimenta o select da tela, e um select fora de ordem faria o
        # usuário procurar "Em Produção" no meio da lista.
        ORCAMENTO = 'orcamento', 'Orçamento'
        CONFIRMADO = 'confirmado', 'Pedido Confirmado'
        AGUARDANDO_ARTE = 'aguardando_arte', 'Aguardando Arte'
        AGUARDANDO_MATERIAL = 'aguardando_material', 'Aguardando Material'
        LIBERADO_PRODUCAO = 'liberado_producao', 'Liberado para Produção'
        EM_PRODUCAO = 'em_producao', 'Em Produção'
        EM_ACABAMENTO = 'em_acabamento', 'Em Acabamento'
        PRONTO = 'pronto', 'Pronto'
        ENTREGUE = 'entregue', 'Entregue'
        CANCELADO = 'cancelado', 'Cancelado'

    # Status que encerram o pedido: não entram na fila de produção e não
    # devem contar como carga de trabalho no PCP.
    STATUS_ENCERRADOS = (Status.ENTREGUE, Status.CANCELADO)

    class Prioridade(models.TextChoices):
        NORMAL = 'normal', 'Normal'
        ALTA = 'alta', 'Alta'
        URGENTE = 'urgente', 'Urgente'

    numero = models.PositiveIntegerField(db_index=True)

    # Token do link público do PDF — o que vai no WhatsApp do cliente.
    # Opaco e longo de propósito: com o número sequencial na URL, trocar um
    # dígito abriria o pedido do vizinho.
    token_publico = models.CharField(
        max_length=32, unique=True, editable=False, blank=True, db_index=True,
    )

    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT,
        related_name='pedidos_moda',
    )
    # A ficha do cliente traz o contato genérico da empresa; o pedido traz
    # quem acompanha ESTE pedido ("INTERFORT (ANDERSON)"). São coisas
    # diferentes, por isso o contato é gravado aqui e não só lido do cadastro.
    contato_nome = models.CharField(max_length=120, blank=True)
    contato_telefone = models.CharField(max_length=30, blank=True)

    data_pedido = models.DateField(default=timezone.localdate, db_index=True)
    data_prevista_entrega = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='A data que o cliente combinou — é dela que sai a priorização do PCP.',
    )

    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pedidos_moda',
    )

    prioridade = models.CharField(
        max_length=10, choices=Prioridade.choices, default=Prioridade.NORMAL,
    )
    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.ORCAMENTO,
        db_index=True,
    )

    observacoes = models.TextField(blank=True)

    # ── Valores ──────────────────────────────────────────────────────────
    # O subtotal NÃO é campo: é a soma dos itens, calculada na leitura. Um
    # campo gravado sairia do lugar toda vez que alguém mexesse num item e
    # esquecesse de recalcular — e um pedido com subtotal errado é pior do
    # que um pedido sem subtotal.
    desconto = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    acrescimo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    frete = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    entrada = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0'),
        help_text='Sinal combinado. Vira a primeira conta a receber, com vencimento na data do pedido.',
    )

    forma_pagamento = models.ForeignKey(
        'financeiro.FormaPagamento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pedidos_moda',
    )
    condicao_pagamento = models.ForeignKey(
        'financeiro.CondicaoPagamento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pedidos_moda',
        help_text='Define em quantas parcelas o saldo é dividido e de quanto em quanto tempo.',
    )

    # Carimbo de quando o financeiro foi gerado. É só para a tela mostrar:
    # a trava de verdade contra gerar duas vezes é a consulta às contas a
    # receber do pedido, que sobrevive a este campo ser limpo à mão.
    financeiro_gerado_em = models.DateTimeField(null=True, blank=True, editable=False)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_pedidos'
        ordering = ['-data_pedido', '-numero']
        unique_together = [('filial', 'numero')]
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['filial', 'data_prevista_entrega']),
        ]
        verbose_name = 'Pedido de produção'
        verbose_name_plural = 'Pedidos de produção'

    def __str__(self):
        return f'#{self.numero:06d} — {self.cliente}'

    def save(self, *args, **kwargs):
        if not self.numero:
            self.numero = self._proximo_numero()
        if not self.token_publico:
            self.token_publico = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)

    def _proximo_numero(self) -> int:
        """
        Próximo número da filial.

        Numeração por filial, e não global, para cada unidade ter a própria
        sequência — é como a confecção referencia o pedido no chão de fábrica.
        """
        # `filial_id`, e não `filial`: filtrar pelo objeto faria o Django
        # carregar a Filial do banco só para descartá-la — uma consulta a
        # mais em toda criação de pedido.
        ultimo = (
            PedidoProducao.all_objects
            .filter(filial_id=self.filial_id)
            .aggregate(models.Max('numero'))['numero__max']
        )
        return (ultimo or 0) + 1

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def encerrado(self) -> bool:
        return self.status in self.STATUS_ENCERRADOS

    @property
    def dias_para_entrega(self):
        """Negativo = atrasado. None quando não há data combinada."""
        if not self.data_prevista_entrega or self.encerrado:
            return None
        return (self.data_prevista_entrega - timezone.localdate()).days

    @property
    def atrasado(self) -> bool:
        dias = self.dias_para_entrega
        return dias is not None and dias < 0

    # ── Valores ──────────────────────────────────────────────────────────

    @property
    def quantidade_total(self) -> int:
        return sum(i.quantidade for i in self.itens.all())

    @property
    def subtotal(self) -> Decimal:
        return sum((i.subtotal for i in self.itens.all()), Decimal('0'))

    @property
    def valor_total(self) -> Decimal:
        """Subtotal − desconto + acréscimo + frete. Nunca negativo."""
        total = (
            self.subtotal
            - (self.desconto or Decimal('0'))
            + (self.acrescimo or Decimal('0'))
            + (self.frete or Decimal('0'))
        )
        return total if total > 0 else Decimal('0')

    @property
    def saldo(self) -> Decimal:
        """O que falta receber depois da entrada. Nunca negativo."""
        resto = self.valor_total - (self.entrada or Decimal('0'))
        return resto if resto > 0 else Decimal('0')

    @property
    def valor_unitario_medio(self) -> Decimal:
        qtd = self.quantidade_total
        if not qtd:
            return Decimal('0')
        return (self.subtotal / qtd).quantize(Decimal('0.01'))

    @property
    def financeiro_gerado(self) -> bool:
        return self.financeiro_gerado_em is not None
