"""
Itens do pedido de produção — o que a ficha lista abaixo do cabeçalho.

Um pedido tem vários itens ("CONJUNTO (CAMISA + CALÇÃO)" é um item; se o
cliente pedir também bonés, é outro).

A quantidade aqui é o total do item. A distribuição por tamanho (PP..G3,
como na grade da ficha) é o bloco seguinte e vai apontar para cá.
"""
from django.db import models

from .cadastros import Modelo


class ItemPedidoProducao(models.Model):
    """Uma peça pedida, com as especificações combinadas com o cliente."""

    pedido = models.ForeignKey(
        'moda.PedidoProducao', on_delete=models.CASCADE, related_name='itens',
    )

    # Produto do catálogo é opcional: a ficha do Grupo Eureka descreve
    # "CONJUNTO (CAMISA + CALÇÃO)", que pode não existir como produto
    # cadastrado. Exigir cadastro prévio travaria o comercial na hora de
    # fechar o pedido, que é justamente quando não se quer atrito.
    produto = models.ForeignKey(
        'moda.ProdutoModa', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_pedido',
        help_text='Do catálogo. Deixe vazio e descreva em "Descrição" se ainda não houver cadastro.',
    )
    descricao = models.CharField(
        max_length=160, blank=True,
        help_text='Ex.: Conjunto — Camisa + Calção. Usado quando não há produto de catálogo.',
    )
    referencia = models.CharField(max_length=40, blank=True)

    modelo = models.ForeignKey(
        'moda.Modelo', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_pedido',
    )
    cor = models.ForeignKey(
        'moda.Cor', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_pedido',
    )
    tecido = models.ForeignKey(
        'moda.Tecido', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_pedido', verbose_name='Tecido / Malha',
    )

    # Gola e manga são do Modelo, mas ficam gravadas aqui também, e não só
    # lidas por FK, por dois motivos:
    #   1. o cliente pode pedir o mesmo modelo com gola diferente;
    #   2. histórico -- corrigir o cadastro do Modelo em 2027 não pode
    #      reescrever o que foi combinado num pedido de 2026.
    # Em branco, `save()` copia o valor do modelo no momento do pedido.
    gola = models.CharField(max_length=20, choices=Modelo.Gola.choices, blank=True)
    manga = models.CharField(max_length=20, choices=Modelo.Manga.choices, blank=True)

    acabamento = models.CharField(
        max_length=120, blank=True,
        help_text='Ex.: barra dobrada, punho em ribana, escudo em patch aplicado.',
    )

    quantidade = models.PositiveIntegerField(default=1)
    observacoes = models.TextField(blank=True)

    ordem = models.PositiveIntegerField(
        default=0, help_text='Posição do item na ficha.',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_itens_pedido'
        ordering = ['ordem', 'id']
        verbose_name = 'Item do pedido'
        verbose_name_plural = 'Itens do pedido'

    def __str__(self):
        return f'{self.nome_exibicao} × {self.quantidade}'

    def save(self, *args, **kwargs):
        # Snapshot de gola e manga a partir do modelo, uma vez. Feito na
        # gravação e não na leitura para o pedido guardar o que valia
        # quando foi fechado.
        if self.modelo_id:
            if not self.gola:
                self.gola = self.modelo.gola or ''
            if not self.manga:
                self.manga = self.modelo.manga or ''
        super().save(*args, **kwargs)

    @property
    def nome_exibicao(self) -> str:
        """O que aparece na ficha: o produto do catálogo ou a descrição livre."""
        if self.produto_id:
            return self.produto.nome
        return self.descricao or 'Item sem descrição'

    @property
    def tecido_exibicao(self) -> str:
        """Tecido do item; sem ele, o do produto de catálogo."""
        if self.tecido_id:
            return str(self.tecido)
        if self.produto_id and self.produto.tecido_id:
            return str(self.produto.tecido)
        return ''
