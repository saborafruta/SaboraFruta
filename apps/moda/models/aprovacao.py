"""
Aprovação do pedido — a interna e a do cliente.

São os dois passos que faltavam no fluxo (7 e 10) e são DUAS COISAS
DIFERENTES, por isso moram no mesmo registro em campos separados:

  · a INTERNA é a liberação da casa: comercial e financeiro conferem preço,
    prazo e condição antes de mandar para o cliente;
  · a DO CLIENTE é o aceite da arte e da grade, feito por ele mesmo no link
    que recebeu — é o que autoriza cortar tecido.

Uma não substitui a outra. Liberar internamente e produzir sem o aceite do
cliente é o erro que faz refazer cem camisas com o escudo errado; esperar o
cliente sem ter conferido o preço é mandar proposta furada.

O CLIENTE TAMBÉM PODE PEDIR AJUSTE, e isso não é "não aprovado ainda": é uma
resposta, com motivo, que precisa voltar para o comercial. Sem esse caminho,
o cliente que não gostou da arte não teria botão nenhum e ligaria — e o
pedido ficaria parado sem ninguém saber por quê.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AprovacaoPedido(models.Model):
    """As duas aprovações de um pedido. Uma por pedido."""

    class Resposta(models.TextChoices):
        PENDENTE = 'pendente', 'Aguardando o cliente'
        APROVADO = 'aprovado', 'Aprovado pelo cliente'
        AJUSTE = 'ajuste', 'Cliente pediu ajuste'

    pedido = models.OneToOneField(
        'moda.PedidoProducao', on_delete=models.CASCADE, related_name='aprovacao',
    )

    # ── Interna ──────────────────────────────────────────────────────────
    liberado_em = models.DateTimeField(null=True, blank=True, editable=False)
    liberado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='pedidos_moda_liberados',
    )
    observacao_interna = models.TextField(blank=True)

    # ── Do cliente ───────────────────────────────────────────────────────
    resposta = models.CharField(
        max_length=10, choices=Resposta.choices, default=Resposta.PENDENTE,
        db_index=True,
    )
    respondido_em = models.DateTimeField(null=True, blank=True, editable=False)
    # Quem respondeu, do lado de lá. Texto livre porque o cliente não tem
    # login: é o nome que ele mesmo digitou, e vale como assinatura do
    # aceite junto com a data e o IP.
    respondido_por = models.CharField(max_length=120, blank=True)
    ip_resposta = models.GenericIPAddressField(null=True, blank=True)
    motivo_ajuste = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_aprovacoes_pedido'
        verbose_name = 'Aprovação do pedido'
        verbose_name_plural = 'Aprovações do pedido'

    def __str__(self):
        return f'Aprovação do pedido {self.pedido_id}'

    # ── Situação ─────────────────────────────────────────────────────────

    @property
    def liberado(self) -> bool:
        return self.liberado_em is not None

    @property
    def aprovado_pelo_cliente(self) -> bool:
        return self.resposta == self.Resposta.APROVADO

    @property
    def pediu_ajuste(self) -> bool:
        return self.resposta == self.Resposta.AJUSTE

    @property
    def aguardando_cliente(self) -> bool:
        """Liberado pela casa e ainda sem resposta do outro lado."""
        return self.liberado and self.resposta == self.Resposta.PENDENTE

    def liberar(self, usuario, observacao: str = '') -> None:
        self.liberado_em = timezone.now()
        self.liberado_por = usuario
        self.observacao_interna = observacao
        self.save(update_fields=[
            'liberado_em', 'liberado_por', 'observacao_interna',
        ])

    def responder(self, resposta: str, nome: str, ip=None, motivo: str = '') -> None:
        """
        Grava o aceite ou o pedido de ajuste do cliente.

        A resposta anterior é sobrescrita de propósito: o cliente que pediu
        ajuste e depois aprovou tem uma posição só, a última. O caminho todo
        fica no histórico de auditoria, que é onde a sequência importa.
        """
        self.resposta = resposta
        self.respondido_em = timezone.now()
        self.respondido_por = (nome or '').strip()[:120]
        self.ip_resposta = ip
        self.motivo_ajuste = motivo if resposta == self.Resposta.AJUSTE else ''
        self.save(update_fields=[
            'resposta', 'respondido_em', 'respondido_por', 'ip_resposta',
            'motivo_ajuste',
        ])
