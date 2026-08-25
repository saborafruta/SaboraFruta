"""
Reserva e requisição de material a partir da necessidade das ordens.

A ponte entre a ficha técnica e o estoque real é o campo
`MaterialFicha.produto_estoque`: sem ele o material da ficha é só texto, e
"estoque atual" não tem onde ser buscado. Material sem essa ligação aparece
na tela com a necessidade calculada e o estoque em branco, dizendo o que
falta ligar — some seria pior, porque a necessidade existe de qualquer
forma.

A REQUISIÇÃO NÃO É UM PEDIDO DE COMPRA. Ela registra "precisamos de tanto
deste material para estas ordens" e para aí. Pedido de compra exige
fornecedor, preço e condição — nada disso o sistema tem como deduzir da
ficha técnica, e inventá-los produziria um documento que o comprador teria
de refazer inteiro. Quem transforma a requisição em pedido é o setor de
compras, escolhendo de quem e por quanto.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models.base import FilialManager, FilialScopedModel


class ReservaMaterial(FilialScopedModel):
    """
    Material separado para uma ordem.

    Aponta para o `Estoque` de verdade: a reserva é feita pelo
    `MovimentacaoService`, que é quem pode mexer em `quantidade_reservada`.
    Este registro existe para saber PARA QUEM cada reserva foi feita — o
    estoque guarda só o total reservado, sem dizer de quem é.
    """

    class Status(models.TextChoices):
        ATIVA = 'ativa', 'Ativa'
        CONSUMIDA = 'consumida', 'Consumida'
        CANCELADA = 'cancelada', 'Cancelada'

    ordem = models.ForeignKey(
        'moda.OrdemProducao', on_delete=models.PROTECT, related_name='reservas',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='reservas_moda',
    )
    # Guardado para a tela mostrar de qual linha da ficha veio a reserva.
    material = models.ForeignKey(
        'moda.MaterialFicha', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reservas',
    )

    quantidade = models.DecimalField(max_digits=12, decimal_places=4)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ATIVA, db_index=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reservas_moda',
    )
    observacao = models.CharField(max_length=160, blank=True)

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_reservas_material'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['ordem']),
        ]
        verbose_name = 'Reserva de material'
        verbose_name_plural = 'Reservas de material'

    def __str__(self):
        return f'{self.produto} — {self.quantidade} para {self.ordem}'


class RequisicaoMaterial(FilialScopedModel):
    """Um pedido do PCP para o setor de compras."""

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Aberta'
        ATENDIDA = 'atendida', 'Atendida'
        CANCELADA = 'cancelada', 'Cancelada'

    numero = models.PositiveIntegerField(db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ABERTA, db_index=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='requisicoes_moda',
    )
    observacao = models.TextField(blank=True)

    # Para onde esta requisição foi. Ligação, e não cópia: o pedido de
    # compra tem vida própria (negociação, aprovação, recebimento), e o
    # que interessa aqui é não gerar duas vezes e saber onde foi parar.
    pedido_compra = models.ForeignKey(
        'compras.PedidoCompra', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='requisicoes_moda',
    )

    objects = FilialManager()
    all_objects = models.Manager()

    class Meta:
        db_table = 'moda_requisicoes_material'
        ordering = ['-numero']
        unique_together = [('filial', 'numero')]
        verbose_name = 'Requisição de material'
        verbose_name_plural = 'Requisições de material'

    def __str__(self):
        return f'Requisição #{self.numero:04d}'

    def save(self, *args, **kwargs):
        if not self.numero:
            ultimo = (
                RequisicaoMaterial.all_objects
                .filter(filial_id=self.filial_id)
                .aggregate(models.Max('numero'))['numero__max']
            )
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    @property
    def total_itens(self) -> int:
        return self.itens.count()


class ItemRequisicao(models.Model):
    """Uma linha da requisição: o que falta e quanto."""

    requisicao = models.ForeignKey(
        RequisicaoMaterial, on_delete=models.CASCADE, related_name='itens',
    )
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_requisicao_moda',
        help_text='Vazio quando o material da ficha ainda não foi ligado ao estoque.',
    )

    # Descrição e código copiados NA GRAVAÇÃO, e não lidos por FK: a
    # requisição é um documento que vai para compras, e precisa continuar
    # dizendo a mesma coisa se alguém editar a ficha técnica depois.
    descricao = models.CharField(max_length=160)
    codigo = models.CharField(max_length=40, blank=True)
    unidade = models.CharField(max_length=6, blank=True)

    quantidade = models.DecimalField(max_digits=12, decimal_places=4)
    observacao = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = 'moda_requisicoes_itens'
        ordering = ['descricao']
        verbose_name = 'Item da requisição'
        verbose_name_plural = 'Itens da requisição'

    def __str__(self):
        return f'{self.descricao}: {self.quantidade} {self.unidade}'.strip()


class ConsumoLoteCorte(models.Model):
    """
    Qual lote REAL de matéria-prima cada corte comeu, e quanto.

    O elo que faltava. `RegistroCorte.lote` é texto livre: o chão de fábrica
    digita o número do rolo, e o estoque nunca soube que rolo era esse. Do
    outro lado, a baixa do corte mexia no saldo do produto sem tocar em lote
    nenhum -- num tecido com lote, `Estoque.quantidade_atual` caía e
    `LoteProduto.quantidade_atual` ficava cheio. Os dois números divergiam em
    silêncio, e quem fosse rastrear um defeito de tecido não tinha por onde
    começar.

    NÃO É UMA SEGUNDA VERDADE sobre o saldo -- esse continua sendo a
    `MovimentacaoEstoque`, que é o razão. Isto é a ALOCAÇÃO: de qual lote saiu
    cada pedaço deste corte. É o que o razão não consegue responder sozinho,
    porque ele é indexado pelo DOCUMENTO, e uma ordem pode ter vários enfestos
    -- estornar um corte pelo razão devolveria tecido dos outros.

    `lote` nulo é consumo que os lotes não cobriram. Guardado assim, e não
    omitido, porque o pedaço sem rastro é justamente o que precisa aparecer.
    """

    corte = models.ForeignKey(
        'moda.RegistroCorte', on_delete=models.CASCADE, related_name='consumos_lote',
    )
    lote = models.ForeignKey(
        'estoque.LoteProduto', on_delete=models.PROTECT, null=True, blank=True,
        related_name='consumos_corte',
        help_text='Vazio quando os lotes não cobriram o consumo.',
    )
    quantidade = models.DecimalField(max_digits=12, decimal_places=4)
    # Copiado na gravação: é o custo daquele rolo NAQUELE dia, e o custo do
    # lote pode ser corrigido depois sem mudar o que a peça custou.
    custo_unitario = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'moda_consumos_lote_corte'
        ordering = ['pk']
        verbose_name = 'Consumo de lote no corte'
        verbose_name_plural = 'Consumos de lote no corte'
        indexes = [
            models.Index(fields=['corte']),
            models.Index(fields=['lote']),
        ]

    def __str__(self):
        onde = self.lote.numero_lote if self.lote_id else 'sem lote'
        return f'{self.quantidade} de {onde}'
