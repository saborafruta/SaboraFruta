from django.contrib import admin

from apps.compras.models import (
    AvaliacaoFornecedor, CalculoTributarioCompra, CotacaoCompra,
    CotacaoCompraFornecedor, CotacaoCompraItem, CotacaoCompraPreco,
    EntradaNF, ItemEntradaNF, ItemPedidoCompra, PedidoCompra,
    RegraTributariaCompra,
)


class ItemPedidoCompraInline(admin.TabularInline):
    model = ItemPedidoCompra
    extra = 0
    autocomplete_fields = ['produto']
    readonly_fields = ['valor_bruto', 'valor_total', 'quantidade_recebida']


@admin.register(PedidoCompra)
class PedidoCompraAdmin(admin.ModelAdmin):
    list_display = [
        'numero_pedido', 'data_emissao', 'fornecedor', 'usuario',
        'valor_total', 'status', 'filial',
    ]
    list_filter = ['status', 'filial']
    search_fields = ['numero_pedido', 'fornecedor__razao_social']
    autocomplete_fields = ['fornecedor', 'filial']
    readonly_fields = [
        'numero_pedido', 'valor_produtos', 'valor_total',
        'data_aprovacao', 'aprovado_por',
    ]
    inlines = [ItemPedidoCompraInline]
    date_hierarchy = 'data_emissao'


class ItemEntradaNFInline(admin.TabularInline):
    model = ItemEntradaNF
    extra = 0
    autocomplete_fields = ['produto', 'apresentacao']
    readonly_fields = ['valor_bruto', 'valor_total', 'lote_gerado']


@admin.register(EntradaNF)
class EntradaNFAdmin(admin.ModelAdmin):
    list_display = [
        'numero_nf', 'serie_nf', 'data_entrada', 'fornecedor',
        'valor_total', 'status', 'filial',
    ]
    list_filter = ['status', 'tipo', 'filial']
    search_fields = ['numero_nf', 'chave_acesso_nf', 'fornecedor__razao_social']
    autocomplete_fields = ['fornecedor', 'pedido_compra', 'filial']
    readonly_fields = [
        'valor_produtos', 'valor_total', 'usuario_efetivacao', 'data_efetivacao',
    ]
    inlines = [ItemEntradaNFInline]
    date_hierarchy = 'data_entrada'


@admin.register(AvaliacaoFornecedor)
class AvaliacaoFornecedorAdmin(admin.ModelAdmin):
    list_display = [
        'fornecedor', 'data_real', 'dias_atraso', 'entregue_no_prazo',
        'nota_pontualidade', 'nota_geral',
    ]
    list_filter = ['entregue_no_prazo', 'filial']
    search_fields = ['fornecedor__razao_social']
    readonly_fields = [
        'fornecedor', 'pedido_compra', 'entrada_nf',
        'data_prevista', 'data_real', 'dias_atraso', 'entregue_no_prazo',
    ]
    date_hierarchy = 'data_real'


@admin.register(RegraTributariaCompra)
class RegraTributariaCompraAdmin(admin.ModelAdmin):
    list_display = [
        'nome', 'empresa', 'data_inicial', 'data_final',
        'regime_comprador', 'regime_fornecedor', 'classe_fiscal', 'ncm_prefixo', 'ativo',
    ]
    list_filter = ['empresa', 'ativo', 'regime_comprador', 'regime_fornecedor', 'classe_fiscal']
    search_fields = ['nome', 'ncm_prefixo']


class CotacaoCompraItemInline(admin.TabularInline):
    model = CotacaoCompraItem
    extra = 0
    readonly_fields = ['produto', 'produto_descricao', 'produto_ncm', 'quantidade']


class CotacaoCompraFornecedorInline(admin.TabularInline):
    model = CotacaoCompraFornecedor
    extra = 0
    readonly_fields = [
        'fornecedor', 'manual_supplier', 'supplier_name', 'supplier_cnpj',
        'supplier_tax_regime', 'supplier_tax_ibs_cbs',
    ]


@admin.register(CotacaoCompra)
class CotacaoCompraAdmin(admin.ModelAdmin):
    list_display = [
        'numero', 'filial', 'usuario', 'data_referencia',
        'valor_nominal_total', 'creditos_estimados_total', 'custo_efetivo_total',
    ]
    list_filter = ['filial', 'data_referencia', 'regime_comprador']
    search_fields = ['numero', 'empresa_snapshot']
    readonly_fields = [
        'numero', 'usuario', 'empresa_snapshot', 'valor_nominal_total',
        'creditos_estimados_total', 'custo_efetivo_total', 'economia_estimada',
    ]
    inlines = [CotacaoCompraItemInline, CotacaoCompraFornecedorInline]


admin.site.register(CotacaoCompraPreco)
admin.site.register(CalculoTributarioCompra)
