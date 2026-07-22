"""Bloco 16 — Vendas PDV, itens, pagamentos, pesagens e devoluções."""
from django.db import models
from apps.core.models import Filial, Usuario
from apps.cadastros.models import Cliente
from apps.core.models.base import TimestampedModel
from apps.core.models.base import FilialManager as FilialAwareManager
from apps.produtos.models import Produto
from .sessao import SessaoPDV
from .caixa import DispositivoPDV


class VendaPDV(TimestampedModel):
    sessao_pdv = models.ForeignKey(SessaoPDV, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendas")
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="vendas_pdv")
    numero_venda = models.BigIntegerField()
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="vendas_pdv")
    cpf_nota = models.CharField(max_length=11, blank=True)

    class StatusDelivery(models.TextChoices):
        NOVO = 'novo', 'Novo Pedido'
        PREPARANDO = 'preparando', 'Em Preparo'
        EM_ENTREGA = 'em_entrega', 'Saiu para Entrega'
        ENTREGUE = 'entregue', 'Entregue'
        CANCELADO = 'cancelado', 'Cancelado'

    status = models.CharField(max_length=20, default="aberta")
    origem = models.CharField(max_length=20, default="pdv")
    delivery = models.BooleanField(default=False)
    endereco_entrega = models.JSONField(default=dict, blank=True)
    status_delivery = models.CharField(
        max_length=20,
        choices=StatusDelivery.choices,
        default=StatusDelivery.NOVO,
        blank=True,
    )
    observacao_delivery = models.TextField(blank=True)
    entregador = models.CharField(max_length=100, blank=True)
    observacao = models.TextField(blank=True, help_text='Observação geral da venda (ex.: entregar após meio-dia).')

    valor_subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_desconto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_acrescimo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pago = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    troco = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    documento_fiscal = models.ForeignKey(
        "financeiro.DocumentoFiscal", on_delete=models.SET_NULL, null=True, blank=True,
    )
    motivo_cancelamento = models.TextField(blank=True)
    cancelado_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="vendas_canceladas",
    )
    cancelado_em = models.DateTimeField(null=True, blank=True)
    requer_autorizacao_cancelamento = models.BooleanField(default=False)

    locked_by_device_serial = models.CharField(max_length=255, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)

    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="vendas_pdv")
    data_venda = models.DateTimeField()

    objects = FilialAwareManager()

    class Meta:
        db_table = "vendas_pdv"
        verbose_name = "Venda PDV"
        verbose_name_plural = "Vendas PDV"
        unique_together = [("filial", "numero_venda")]
        ordering = ["-data_venda"]

    def __str__(self):
        return f"Venda PDV #{self.numero_venda}"


class PesagemPDV(models.Model):
    sessao_pdv = models.ForeignKey(SessaoPDV, on_delete=models.PROTECT, related_name="pesagens")
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    dispositivo = models.ForeignKey(DispositivoPDV, on_delete=models.SET_NULL, null=True, blank=True)
    produto = models.ForeignKey(Produto, on_delete=models.SET_NULL, null=True, blank=True)
    codigo_balanca = models.CharField(max_length=5, blank=True)
    peso_bruto = models.DecimalField(max_digits=10, decimal_places=3)
    tara = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    peso_liquido = models.DecimalField(max_digits=10, decimal_places=3)
    variacao_percentual = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    dentro_tolerancia = models.BooleanField(default=True)
    numero_serie_balanca = models.CharField(max_length=60, blank=True)
    raw_resposta = models.TextField(blank=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    data_pesagem = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pesagens_pdv"
        verbose_name = "Pesagem PDV"
        verbose_name_plural = "Pesagens PDV"
        ordering = ["-data_pesagem"]


class ItemVendaPDV(models.Model):
    venda_pdv = models.ForeignKey(VendaPDV, on_delete=models.CASCADE, related_name="itens")
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    lote = models.ForeignKey(
        "estoque.LoteProduto", on_delete=models.SET_NULL, null=True, blank=True,
    )
    numero_item = models.PositiveSmallIntegerField()
    tipo_venda = models.CharField(max_length=20, default="unitario")
    pesagem = models.ForeignKey(PesagemPDV, on_delete=models.SET_NULL, null=True, blank=True)
    peso_bruto = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    tara = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    peso_liquido = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    unidade_medida = models.CharField(max_length=6)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    valor_unitario_tabela = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    custo_unitario_snapshot = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    preco_origem = models.CharField(max_length=30, blank=True, default="")
    preco_origem_detalhe = models.TextField(blank=True, default="")
    desconto_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    desconto_valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    acrescimo_valor = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    estoque_baixado = models.BooleanField(default=False)
    movimentacoes_estoque_ids = models.JSONField(default=list, blank=True)

    cfop = models.CharField(max_length=5, blank=True)
    cst_icms = models.CharField(max_length=3, blank=True)
    aliquota_icms = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    valor_icms = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_st = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ipi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_fcp = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_fcpst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_pis = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cofins = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_ibs = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    valor_cbs = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    data_validade_produto = models.DateField(null=True, blank=True)
    voucher_pacote = models.BooleanField(default=False)
    desconto_manual = models.BooleanField(default=False)
    motivo_desconto = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "itens_venda_pdv"
        verbose_name = "Item de venda PDV"
        verbose_name_plural = "Itens de venda PDV"
        ordering = ["venda_pdv", "numero_item"]


class PagamentoVendaPDV(models.Model):
    venda_pdv = models.ForeignKey(VendaPDV, on_delete=models.CASCADE, related_name="pagamentos")
    forma_pagamento = models.ForeignKey("financeiro.FormaPagamento", on_delete=models.PROTECT)
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    troco = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tef_transacao = models.ForeignKey(
        "financeiro.TEFTransacao", on_delete=models.SET_NULL, null=True, blank=True,
    )
    nsu = models.CharField(max_length=30, blank=True)
    autorizacao = models.CharField(max_length=20, blank=True)
    bandeira = models.CharField(max_length=30, blank=True)
    numero_parcelas = models.PositiveSmallIntegerField(default=1)
    pix_txid = models.CharField(max_length=35, blank=True)
    pix_e2e_id = models.CharField(max_length=35, blank=True)
    nosso_numero = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, default="aprovado")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pagamentos_venda_pdv"
        verbose_name = "Pagamento de venda PDV"
        verbose_name_plural = "Pagamentos de venda PDV"


class DevolucaoPDV(models.Model):
    venda_pdv = models.ForeignKey(VendaPDV, on_delete=models.PROTECT, related_name="devolucoes")
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT)
    motivo = models.TextField()
    tipo_estorno = models.CharField(max_length=20, blank=True)
    valor_estorno = models.DecimalField(max_digits=14, decimal_places=2)
    documento_fiscal = models.ForeignKey(
        "financeiro.DocumentoFiscal", on_delete=models.SET_NULL, null=True, blank=True,
    )
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="devolucoes_realizadas")
    autorizado_por = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="devolucoes_autorizadas",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "devolucoes_pdv"
        verbose_name = "Devolução PDV"
        verbose_name_plural = "Devoluções PDV"
        ordering = ["-created_at"]


class ItemDevolucaoPDV(models.Model):
    devolucao = models.ForeignKey(DevolucaoPDV, on_delete=models.CASCADE, related_name="itens")
    item_venda = models.ForeignKey(ItemVendaPDV, on_delete=models.PROTECT)
    produto = models.ForeignKey(Produto, on_delete=models.PROTECT)
    quantidade_devolvida = models.DecimalField(max_digits=12, decimal_places=3)
    valor_unitario = models.DecimalField(max_digits=14, decimal_places=4)
    valor_total = models.DecimalField(max_digits=14, decimal_places=2)
    motivo_item = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "itens_devolucao_pdv"


class PDVCache(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=30,
                             help_text="top_produtos|pendencias|clientes_frequentes")
    dados = models.JSONField()
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pdv_cache"
        unique_together = [("filial", "tipo")]
