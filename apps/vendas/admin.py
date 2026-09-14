from django.contrib import admin

from apps.vendas.models import (
    DevolucaoVenda, ItemDevolucao, ItemPedidoVenda, ItemSeparacao,
    PedidoVenda, SeparacaoPedido,
)


class ItemPedidoVendaInline(admin.TabularInline):
    model = ItemPedidoVenda
    extra = 0
    autocomplete_fields = ['produto']
    readonly_fields = ['valor_bruto', 'valor_total', 'custo_unitario_snapshot', 'quantidade_atendida']


@admin.register(PedidoVenda)
class PedidoVendaAdmin(admin.ModelAdmin):
    list_display = [
        'numero_pedido', 'data_emissao', 'cliente', 'usuario',
        'valor_total', 'status', 'filial',
    ]
    list_filter = ['status', 'tipo', 'origem', 'filial']
    search_fields = ['numero_pedido', 'cliente__razao_social', 'cliente__nome_fantasia']
    autocomplete_fields = ['cliente', 'representante', 'tabela_preco', 'transportadora', 'filial']
    readonly_fields = [
        'numero_pedido', 'valor_produtos', 'valor_total',
        'data_aprovacao', 'aprovado_por',
    ]
    inlines = [ItemPedidoVendaInline]
    date_hierarchy = 'data_emissao'


class ItemSeparacaoInline(admin.TabularInline):
    model = ItemSeparacao
    extra = 0
    readonly_fields = ['item_pedido', 'lote', 'quantidade_separada']


@admin.register(SeparacaoPedido)
class SeparacaoPedidoAdmin(admin.ModelAdmin):
    list_display = ['numero', 'pedido', 'status', 'usuario_separador', 'data_inicio', 'data_fim']
    list_filter = ['status', 'filial']
    search_fields = ['numero', 'pedido__numero_pedido']
    inlines = [ItemSeparacaoInline]


class ItemDevolucaoInline(admin.TabularInline):
    model = ItemDevolucao
    extra = 0
    readonly_fields = [
        'item_pedido', 'quantidade', 'apresentacao', 'quantidade_comercial',
        'valor_unitario', 'valor_total',
    ]


@admin.register(DevolucaoVenda)
class DevolucaoVendaAdmin(admin.ModelAdmin):
    list_display = ['numero', 'pedido', 'motivo', 'data_devolucao', 'valor_total', 'status']
    list_filter = ['status', 'motivo', 'filial']
    search_fields = ['numero', 'pedido__numero_pedido']
    inlines = [ItemDevolucaoInline]
