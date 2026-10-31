"""
Pedido de Venda — mapeia `pedidos_venda` e `itens_pedido_venda` do banco.
Ciclo: rascunho → aguardando_aprovacao → aprovado → confirmado → em_separacao
       → faturado → entregue | cancelado | devolvido
"""
from django.db import models
from django.db.models import Q

from apps.core.models.base import FilialScopedModel, TimestampedModel


class PedidoVenda(FilialScopedModel):

    class Tipo(models.TextChoices):
        ORCAMENTO = 'orcamento', 'Orçamento'
        PEDIDO = 'pedido', 'Pedido'
        VENDA_BALCAO = 'venda_balcao', 'Venda Balcão'
        CONSIGNACAO = 'consignacao', 'Consignação'
        REMESSA = 'remessa', 'Remessa'

    class Status(models.TextChoices):
        RASCUNHO = 'rascunho', 'Rascunho'
        AGUARDANDO_APROVACAO = 'aguardando_aprovacao', 'Aguardando Aprovação'
        APROVADO = 'aprovado', 'Aprovado'
        CONFIRMADO = 'confirmado', 'Aguardando Separação'
        EM_SEPARACAO = 'em_separacao', 'Separado'
        FATURADO = 'faturado', 'Faturado'
        PARCIALMENTE_FATURADO = 'parcialmente_faturado', 'Parcialmente Faturado'
        EM_ENTREGA = 'em_entrega', 'Em Entrega'
        ENTREGUE = 'entregue', 'Entregue'
        CANCELADO = 'cancelado', 'Cancelado'
        DEVOLVIDO = 'devolvido', 'Devolvido'

    class Origem(models.TextChoices):
        PDV = 'pdv', 'PDV'
        BALCAO = 'balcao', 'Balcão'
        EXTERNO = 'externo', 'Externo'
        API = 'api', 'API'
        ECOMMERCE = 'ecommerce', 'E-commerce'
        WHATSAPP = 'whatsapp', 'WhatsApp'
        TELEFONE = 'telefone', 'Telefone'

    ORIGENS_FORCA_VENDAS = (Origem.EXTERNO, Origem.WHATSAPP)

    class Prioridade(models.TextChoices):
        NORMAL = 'normal', 'Normal'
        URGENTE = 'urgente', 'Urgente'
        VIP = 'vip', 'VIP'

    class ModalidadeFrete(models.IntegerChoices):
        CIF = 0, '0 - CIF (por conta do emitente)'
        FOB = 1, '1 - FOB (por conta do destinatário)'
        TERCEIROS = 2, '2 - Terceiros'
        REMETENTE = 3, '3 - Remetente'
        DESTINATARIO = 4, '4 - Destinatário'
        SEM_FRETE = 9, '9 - Sem frete'

    numero_pedido = models.CharField(max_length=20, db_index=True)
    idempotency_key = models.CharField(
        max_length=64,
        blank=True,
        help_text='Identificador único enviado por clientes offline para evitar pedidos duplicados.',
    )

    # Partes envolvidas
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT, related_name='pedidos',
    )
    representante = models.ForeignKey(
        'cadastros.Representante', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pedidos',
    )
    transportadora = models.ForeignKey(
        'cadastros.Transportadora', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    veiculo = models.ForeignKey(
        'cadastros.VeiculoTransportadora', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    usuario = models.ForeignKey(
        'core.Usuario', on_delete=models.PROTECT, related_name='pedidos_criados',
    )

    # Comercial
    tabela_preco = models.ForeignKey(
        'produtos.TabelaPreco', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    forma_pagamento = models.ForeignKey(
        'financeiro.FormaPagamento', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pedidos_venda',
    )
    condicao_pagamento = models.ForeignKey(
        'financeiro.CondicaoPagamento', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pedidos_venda',
    )
    prazo_pagamento_dias = models.PositiveSmallIntegerField(default=0)
    pagamento_confirmado = models.BooleanField(default=False)
    referencia_pagamento = models.CharField(
        max_length=500, blank=True,
        help_text='Link de pagamento, identificação do PIX ou outra referência.',
    )

    # Tipo e status
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.PEDIDO)
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.RASCUNHO, db_index=True,
    )
    origem = models.CharField(
        max_length=20, choices=Origem.choices, default=Origem.BALCAO,
    )
    prioridade = models.CharField(
        max_length=10, choices=Prioridade.choices, default=Prioridade.NORMAL,
    )

    # Endereço de entrega (pode ser avulso)
    endereco_entrega = models.ForeignKey(
        'cadastros.ClienteEndereco', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    endereco_entrega_avulso = models.JSONField(null=True, blank=True)

    # Datas
    data_emissao = models.DateTimeField(auto_now_add=False, db_index=True)
    data_validade_orcamento = models.DateField(null=True, blank=True)
    data_entrega_prevista = models.DateField(null=True, blank=True)
    data_entrega_realizada = models.DateField(null=True, blank=True)
    data_saida_entrega = models.DateTimeField(null=True, blank=True)
    responsavel_entrega = models.CharField(max_length=120, blank=True)
    observacao_entrega = models.TextField(blank=True)

    # Frete
    modalidade_frete = models.SmallIntegerField(
        choices=ModalidadeFrete.choices, default=ModalidadeFrete.CIF,
    )
    valor_frete = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Valores
    valor_produtos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    desconto_percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Campo legado mantido para compatibilidade do banco.',
    )
    desconto_valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text='Campo legado mantido em sincronia com valor_desconto.',
    )
    valor_acrescimo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    acrescimo = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text='Campo legado mantido em sincronia com valor_acrescimo.',
    )
    valor_outras_despesas = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # Aprovação
    aprovado_por = models.ForeignKey(
        'core.Usuario', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='pedidos_aprovados',
    )
    data_aprovacao = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.TextField(blank=True)

    observacao = models.TextField(blank=True)
    observacao_interna = models.TextField(blank=True)

    class Meta:
        db_table = 'pedidos_venda'
        ordering = ['-data_emissao']
        indexes = [
            models.Index(fields=['filial', 'status']),
            models.Index(fields=['cliente', '-data_emissao']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['filial', 'idempotency_key'],
                condition=~Q(idempotency_key=''),
                name='pedido_venda_idempotencia_filial_unica',
            ),
        ]
        verbose_name = 'Pedido de Venda'
        verbose_name_plural = 'Pedidos de Venda'

    def __str__(self):
        return f'Pedido {self.numero_pedido} — {self.cliente}'

    # Transições permitidas
    @property
    def pode_confirmar(self):
        return self.status in (self.Status.RASCUNHO, self.Status.APROVADO)

    @property
    def pode_separar(self):
        return self.status == self.Status.CONFIRMADO

    @property
    def pode_faturar(self):
        if self.origem in self.ORIGENS_FORCA_VENDAS:
            return self.status == self.Status.EM_SEPARACAO
        return self.status in (self.Status.CONFIRMADO, self.Status.EM_SEPARACAO)

    @property
    def pode_iniciar_entrega(self):
        return self.status in (self.Status.FATURADO, self.Status.PARCIALMENTE_FATURADO)

    @property
    def pode_cancelar(self):
        return self.status in (
            self.Status.RASCUNHO, self.Status.AGUARDANDO_APROVACAO,
            self.Status.APROVADO, self.Status.CONFIRMADO, self.Status.EM_SEPARACAO,
        )

    def recalcular_totais(self):
        """Recalcula valor_produtos e valor_total a partir dos itens."""
        from django.db.models import Sum
        agg = self.itens.aggregate(
            total_produtos=Sum('valor_total'),
            total_desconto=Sum('valor_desconto'),
        )
        self.valor_produtos = agg['total_produtos'] or 0
        self.valor_desconto = agg['total_desconto'] or 0
        self.desconto_percentual = 0
        self.desconto_valor = self.valor_desconto
        self.acrescimo = self.valor_acrescimo
        self.valor_total = (
            self.valor_produtos
            + self.valor_frete
            + self.valor_acrescimo
            + self.valor_outras_despesas
            + self.valor_ipi
            + self.valor_st
            - self.valor_desconto
        )


class ItemPedidoVenda(TimestampedModel):
    """Linha de produto num pedido de venda."""

    pedido = models.ForeignKey(PedidoVenda, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(
        'produtos.Produto', on_delete=models.PROTECT, related_name='+',
    )
    apresentacao = models.ForeignKey(
        'produtos.ProdutoApresentacao', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    apresentacao_descricao = models.CharField(max_length=120, blank=True)
    apresentacao_unidade = models.CharField(max_length=20, blank=True)
    apresentacao_fator_conversao = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True,
    )
    quantidade_comercial = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True,
    )
    valor_unitario_comercial = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True,
    )
    numero_item = models.SmallIntegerField(default=1)

    # Preço e quantidade
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    quantidade_solicitada = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
        help_text='Quantidade originalmente solicitada, preservada após a conferência.',
    )
    quantidade_atendida = models.DecimalField(
        max_digits=12, decimal_places=3, default=0,
        help_text='Quantidade efetivamente separada/faturada',
    )
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    valor_bruto = models.DecimalField(max_digits=14, decimal_places=2)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    percentual_desconto = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    desconto_percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Campo legado mantido em sincronia com percentual_desconto.',
    )
    desconto_valor = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text='Campo legado mantido em sincronia com valor_desconto.',
    )
    valor_acrescimo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    quantidade_reservada = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text='Campo legado sincronizado ao confirmar/liberar reserva.',
    )
    quantidade_faturada = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        help_text='Campo legado sincronizado ao faturar o pedido.',
    )

    # Custo snapshot no momento do pedido (para análise de margem)
    custo_unitario_snapshot = models.DecimalField(
        max_digits=14, decimal_places=4, default=0,
        help_text='Custo médio do produto no momento da venda',
    )

    observacao = models.CharField(max_length=255, blank=True)
    separacao_conferida = models.BooleanField(default=False)
    observacao_separacao = models.CharField(max_length=255, blank=True)
    deposito_separacao = models.ForeignKey(
        'estoque.Deposito', on_delete=models.PROTECT, null=True, blank=True,
        related_name='itens_pedido_separados',
        help_text='Depósito físico escolhido pelo estoquista para retirar o item.',
    )
    item_substituido = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='itens_substitutos',
        help_text='Item original que este produto substituiu durante a separação.',
    )

    class Meta:
        db_table = 'itens_pedido_venda'
        ordering = ['pedido', 'numero_item']
        indexes = [
            models.Index(fields=['pedido', 'numero_item']),
        ]

    def __str__(self):
        return f'{self.pedido.numero_pedido} item {self.numero_item}: {self.produto}'

    def calcular_totais(self):
        """Calcula valor_bruto e valor_total."""
        self.valor_bruto = self.quantidade * self.valor_unitario
        if self.percentual_desconto and not self.valor_desconto:
            self.valor_desconto = self.valor_bruto * (self.percentual_desconto / 100)
        self.desconto_percentual = self.percentual_desconto
        self.desconto_valor = self.valor_desconto
        self.valor_total = self.valor_bruto - self.valor_desconto + self.valor_acrescimo
