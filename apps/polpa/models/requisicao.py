"""
A requisição de insumo: o que a produção pede e compras resolve.

NÃO É UM PEDIDO DE COMPRA. Fornecedor, preço, prazo e condição não saem da
ficha técnica — inventá-los produziria um documento que o comprador teria de
refazer inteiro, e um pedido errado circulando é pior que nenhum. A requisição
diz O QUE FALTA e quanto; compras decide de quem e por quanto.

O DOCUMENTO É UM SÓ PARA VÁRIAS ORDENS. Três batidas de acerola na semana
pedem morango três vezes; comprar três vezes custa frete três vezes e perde a
escala da negociação. A necessidade é somada por insumo antes de virar linha.

O QUE FOI PEDIDO FICA CONGELADO NA LINHA. Descrição, código e unidade são
copiados na gravação e não lidos por chave estrangeira: a requisição vai para
compras e precisa continuar dizendo a mesma coisa se alguém corrigir o
cadastro do produto depois — senão o comprador negocia uma coisa e recebe
outra.
"""
from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class RequisicaoInsumo(FilialScopedModel):
    """Um pedido do PCP da polpa para o setor de compras."""

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Aberta'
        ATENDIDA = 'atendida', 'Atendida'
        CANCELADA = 'cancelada', 'Cancelada'

    numero = models.PositiveIntegerField(db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ABERTA,
        db_index=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='requisicoes_polpa',
    )
    observacao = models.TextField(blank=True)

    # LIGAÇÃO, E NÃO CÓPIA. O pedido de compra tem vida própria — negociação,
    # aprovação, recebimento — e o que interessa aqui é não gerar duas vezes e
    # saber onde a requisição foi parar.
    pedido_compra = models.ForeignKey(
        'compras.PedidoCompra', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='requisicoes_polpa',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'polpa_requisicoes_insumo'
        ordering = ['-numero']
        unique_together = [('filial', 'numero')]
        verbose_name = 'Requisição de insumo'
        verbose_name_plural = 'Requisições de insumo'

    def __str__(self):
        return f'Requisição #{self.numero:04d}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = (
                RequisicaoInsumo.all_objects
                .filter(filial_id=self.filial_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    @property
    def aberta(self) -> bool:
        return self.status == self.Status.ABERTA

    @property
    def virou_compra(self) -> bool:
        return self.pedido_compra_id is not None


class ItemRequisicaoInsumo(models.Model):
    """Uma linha do que falta comprar."""

    requisicao = models.ForeignKey(
        RequisicaoInsumo, on_delete=models.CASCADE, related_name='itens',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT,
        related_name='itens_requisicao_polpa',
    )

    # Copiados NA GRAVAÇÃO: a requisição precisa continuar dizendo a mesma
    # coisa se alguém corrigir o cadastro depois.
    descricao = models.CharField(max_length=160)
    codigo = models.CharField(max_length=40, blank=True)
    unidade = models.CharField(max_length=6, blank=True)

    quantidade = models.DecimalField(max_digits=12, decimal_places=4)
    # Os números que explicam a linha, para o comprador não ter de voltar ao
    # PCP perguntar de onde saiu o pedido.
    necessario = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    disponivel = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    observacao = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = 'polpa_requisicoes_insumo_itens'
        ordering = ['descricao']
        verbose_name = 'Item da requisição'
        verbose_name_plural = 'Itens da requisição'

    def __str__(self):
        return f'{self.descricao}: {self.quantidade} {self.unidade}'.strip()
