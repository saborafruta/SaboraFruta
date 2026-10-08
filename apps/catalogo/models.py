import secrets

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models.base import FilialScopedModel, TimestampedModel


def gerar_token():
    return secrets.token_urlsafe(24)


class CatalogoConfiguracao(TimestampedModel):
    class FreteAbaixoLimite(models.TextChoices):
        FIXO = 'fixo', 'Valor fixo'
        A_COMBINAR = 'a_combinar', 'A combinar com o cliente'
        GRATIS = 'gratis', 'Sempre grátis'

    filial = models.OneToOneField(
        'core.Filial', on_delete=models.PROTECT, related_name='configuracao_catalogo',
    )
    ativo = models.BooleanField(default=True)
    titulo = models.CharField(max_length=120, blank=True)
    descricao = models.CharField(max_length=240, blank=True)
    pedido_minimo = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    retirada_ativa = models.BooleanField(default=True)
    entrega_ativa = models.BooleanField(default=True)
    agendamento_entrega_ativo = models.BooleanField(default=True)
    frete_gratis_ativo = models.BooleanField(default=False)
    valor_minimo_frete_gratis = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    frete_abaixo_limite = models.CharField(
        max_length=20, choices=FreteAbaixoLimite.choices,
        default=FreteAbaixoLimite.A_COMBINAR,
    )
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    prazo_minimo_entrega_horas = models.PositiveSmallIntegerField(default=1)

    class Meta:
        db_table = 'catalogo_configuracoes'

    def __str__(self):
        return f'Catálogo — {self.filial}'

    def calcular_frete(self, subtotal, modalidade):
        if modalidade == PedidoCatalogo.Modalidade.RETIRADA:
            return 0, False
        if self.frete_gratis_ativo and subtotal >= self.valor_minimo_frete_gratis:
            return 0, False
        if self.frete_abaixo_limite == self.FreteAbaixoLimite.FIXO:
            return self.valor_frete, False
        if self.frete_abaixo_limite == self.FreteAbaixoLimite.GRATIS:
            return 0, False
        return 0, True


class CatalogoLinkPublico(TimestampedModel):
    filial = models.OneToOneField(
        'core.Filial', on_delete=models.CASCADE, related_name='link_catalogo_publico',
    )
    token = models.CharField(max_length=80, unique=True, default=gerar_token)
    ativo = models.BooleanField(default=True)

    class Meta:
        db_table = 'catalogo_links_publicos'


class PedidoCatalogo(FilialScopedModel):
    class Status(models.TextChoices):
        AGUARDANDO_CLIENTE = 'aguardando_cliente', 'Aguardando confirmação do cliente'
        AGUARDANDO_LOJA = 'aguardando_loja', 'Aguardando aprovação da loja'
        APROVADO = 'aprovado', 'Aprovado'
        EM_SEPARACAO = 'em_separacao', 'Em separação'
        PENDENTE_CAIXA = 'pendente_caixa', 'Pendente no caixa'
        PAGO = 'pago', 'Pagamento confirmado'
        PRONTO = 'pronto', 'Pronto para entrega'
        SAIU_ENTREGA = 'saiu_entrega', 'Saiu para entrega'
        ENTREGUE = 'entregue', 'Entregue'
        CANCELADO = 'cancelado', 'Cancelado'

    class Modalidade(models.TextChoices):
        ENTREGA = 'entrega', 'Entrega'
        RETIRADA = 'retirada', 'Retirada na loja'

    class Pagamento(models.TextChoices):
        PIX = 'pix', 'Pix'
        DINHEIRO = 'dinheiro', 'Dinheiro na entrega'
        CARTAO = 'cartao', 'Cartão na entrega'
        A_COMBINAR = 'a_combinar', 'A combinar'

    numero = models.CharField(max_length=24, db_index=True)
    token = models.CharField(max_length=80, unique=True, default=gerar_token)
    cliente = models.ForeignKey(
        'cadastros.Cliente', on_delete=models.PROTECT, related_name='pedidos_catalogo',
    )
    conversa_id = models.PositiveBigIntegerField(null=True, blank=True)
    pedido_venda = models.OneToOneField(
        'vendas.PedidoVenda', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='origem_catalogo',
    )
    venda_pdv = models.OneToOneField(
        'pdv.VendaPDV', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='origem_catalogo',
    )
    status = models.CharField(max_length=28, choices=Status.choices, default=Status.AGUARDANDO_CLIENTE)
    nome_cliente = models.CharField(max_length=150)
    telefone = models.CharField(max_length=15, db_index=True)
    modalidade = models.CharField(max_length=12, choices=Modalidade.choices)
    forma_pagamento = models.CharField(max_length=20, choices=Pagamento.choices)
    troco_para = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    endereco_entrega = models.JSONField(default=dict, blank=True)
    entrega_em = models.DateTimeField(null=True, blank=True)
    observacao = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    valor_frete = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    frete_a_combinar = models.BooleanField(default=False)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    confirmado_cliente_em = models.DateTimeField(null=True, blank=True)
    aprovado_loja_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'catalogo_pedidos'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['filial', 'status']), models.Index(fields=['telefone', '-created_at'])]

    def __str__(self):
        return f'{self.numero} — {self.nome_cliente}'


class ItemPedidoCatalogo(TimestampedModel):
    pedido = models.ForeignKey(PedidoCatalogo, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey('produtos.Produto', on_delete=models.PROTECT, related_name='+')
    descricao = models.CharField(max_length=150)
    quantidade = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    valor_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = 'catalogo_pedido_itens'
        ordering = ['id']
