"""
Itens do pedido de produção — o que a ficha lista abaixo do cabeçalho.

Um pedido tem vários itens ("CONJUNTO (CAMISA + CALÇÃO)" é um item; se o
cliente pedir também bonés, é outro).

A quantidade aqui é o total do item. A distribuição por tamanho (PP..G3,
como na grade da ficha) é o bloco seguinte e vai apontar para cá.
"""
from decimal import Decimal

from django.db import models

from .cadastros import Modelo


class ItemPedidoProducao(models.Model):
    """Uma peça pedida, com as especificações combinadas com o cliente."""

    class StatusFluxo(models.TextChoices):
        ORCAMENTO = 'orcamento', 'Orçamento'
        APROVADO = 'aprovado', 'Pedido aprovado'
        PRODUCAO = 'producao', 'Produção'
        PRONTO = 'pronto', 'Pronto para retirada'
        ENTREGUE = 'entregue', 'Entregue'

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
    # A GRADE DE TAMANHO DESTE ITEM, quando o pedido leva o mesmo produto em
    # mais de uma (ex.: a mesma camisa em Adulto e em OverSize).
    #
    # Precisa ser uma linha por grade, não uma linha só: a quantidade mora
    # em `ItemGradePedido`, com `unique_together ('item','tamanho')`, e as
    # grades da casa compartilham os MESMOS registros de Tamanho (a sigla é
    # única por filial). Num item só, "Adulto G = 5" e "OverSize G = 3"
    # colidiriam na mesma chave e um apagaria o outro. Separadas também é
    # como a produção lê: são cortes diferentes.
    #
    # O NOME NÃO PODE SER `grade`: esse já é o acessor reverso de
    # `ItemGradePedido.item` (`related_name='grade'`), que é por onde o
    # resto do sistema lê as quantidades do item -- inclusive o prefetch
    # `itens__grade__tamanho`. Chamar o campo de `grade` derruba o Django no
    # system check (fields.E302/E303), antes mesmo de rodar migration.
    #
    # SET_NULL para apagar uma grade do cadastro não levar junto o histórico
    # do pedido; nulo é o item lançado sem grade, que é o caso de sempre.
    grade_tamanho = models.ForeignKey(
        'moda.Grade', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='itens_pedido', verbose_name='Grade de tamanho',
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
    status_fluxo = models.CharField(
        max_length=15, choices=StatusFluxo.choices,
        default=StatusFluxo.ORCAMENTO, db_index=True,
    )
    quantidade_entregue = models.PositiveIntegerField(default=0)

    # Preço fechado com o cliente para ESTA peça neste pedido. Fica no item
    # e não no produto porque confecção negocia por pedido: o mesmo modelo
    # sai a um preço para um time de 40 peças e a outro para um de 200.
    valor_unitario = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0'),
        verbose_name='Valor unitário',
    )

    observacoes = models.TextField(blank=True)

    configuracao_conjunto = models.JSONField(
        default=dict, blank=True,
        help_text=(
            'Ficha interna do conjunto esportivo: estrutura e grades '
            'independentes da camisa e do calção.'
        ),
    )

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
        """
        O que aparece na ficha: o produto do catálogo ou a descrição livre.

        Com grade, ela entra no nome. O mesmo produto em Adulto e em
        OverSize são duas linhas, e sem a grade no nome as duas sairiam
        idênticas na tela, na tabela de grade e no seletor de copiar --
        sem como saber qual é qual.
        """
        base = self.produto.nome if self.produto_id else (self.descricao or 'Item sem descrição')
        if self.grade_tamanho_id:
            return f'{base} — {self.grade_tamanho.nome}'
        return base

    @property
    def subtotal(self) -> Decimal:
        """Quantidade × valor unitário. Base de tudo na seção de valores."""
        return (self.valor_unitario or Decimal('0')) * self.quantidade

    @property
    def quantidade_pendente(self) -> int:
        return max(0, self.quantidade - self.quantidade_entregue)

    @property
    def entrega_parcial(self) -> bool:
        return 0 < self.quantidade_entregue < self.quantidade

    @property
    def tecido_exibicao(self) -> str:
        """Tecido do item; sem ele, o do produto de catálogo."""
        if self.tecido_id:
            return str(self.tecido)
        if self.produto_id and self.produto.tecido_id:
            return str(self.produto.tecido)
        return ''

    @property
    def eh_conjunto(self) -> bool:
        return bool(self.configuracao_conjunto)

    @property
    def componentes_conjunto(self):
        if not self.eh_conjunto:
            return []
        from ..services.conjunto import componentes_conjunto_exibicao
        return componentes_conjunto_exibicao(self)
