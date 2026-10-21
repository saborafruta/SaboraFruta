from django.contrib import admin

from apps.estoque.models import (
    AlertaVencimento, Deposito, EnderecoEstoque, Estoque, Inventario,
    ItemInventario, LoteProduto, MovimentacaoEstoque, SaldoEndereco,
)


@admin.register(Deposito)
class DepositoAdmin(admin.ModelAdmin):
    list_display = [
        'nome', 'filial', 'tipo', 'is_padrao', 'permite_venda',
        'permite_producao', 'ativo',
    ]
    list_filter = ['filial', 'tipo', 'is_padrao', 'ativo']
    search_fields = ['nome', 'filial__razao_social', 'filial__nome_fantasia']
    autocomplete_fields = ['filial']


@admin.register(EnderecoEstoque)
class EnderecoEstoqueAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'descricao', 'deposito', 'filial', 'ativo']
    list_filter = ['filial', 'deposito', 'ativo']
    search_fields = ['codigo', 'descricao', 'deposito__nome']
    autocomplete_fields = ['deposito', 'filial']


@admin.register(SaldoEndereco)
class SaldoEnderecoAdmin(admin.ModelAdmin):
    list_display = ['estoque', 'endereco', 'quantidade', 'prioridade_separacao', 'updated_at']
    list_filter = ['endereco__filial', 'endereco__deposito']
    search_fields = ['estoque__produto__descricao', 'endereco__codigo']
    autocomplete_fields = ['estoque', 'endereco']


@admin.register(LoteProduto)
class LoteProdutoAdmin(admin.ModelAdmin):
    list_display = [
        'numero_lote', 'produto', 'filial', 'data_validade',
        'quantidade_atual', 'status',
    ]
    list_filter = ['status', 'filial', 'data_validade']
    search_fields = ['numero_lote', 'produto__descricao', 'produto__codigo']
    autocomplete_fields = ['produto', 'filial', 'fornecedor']
    readonly_fields = ['quantidade_atual', 'created_at', 'updated_at']


@admin.register(Estoque)
class EstoqueAdmin(admin.ModelAdmin):
    list_display = [
        'produto', 'filial', 'deposito', 'quantidade_atual',
        'quantidade_reservada', 'quantidade_disponivel', 'custo_medio',
    ]
    list_filter = ['filial', 'deposito']
    search_fields = ['produto__descricao', 'produto__codigo']
    readonly_fields = [
        'quantidade_atual', 'quantidade_reservada', 'quantidade_disponivel',
        'custo_medio', 'ultima_entrada', 'ultima_saida', 'updated_at',
    ]

    def has_add_permission(self, request):
        return False  # Estoque só é criado via MovimentacaoService

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MovimentacaoEstoque)
class MovimentacaoEstoqueAdmin(admin.ModelAdmin):
    list_display = [
        'data_movimentacao', 'tipo_operacao', 'produto', 'filial', 'deposito',
        'quantidade', 'usuario',
    ]
    list_filter = ['tipo_operacao', 'documento_tipo', 'filial', 'deposito']
    search_fields = ['produto__descricao', 'documento_numero', 'observacao']
    readonly_fields = [f.name for f in MovimentacaoEstoque._meta.fields]
    date_hierarchy = 'data_movimentacao'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertaVencimento)
class AlertaVencimentoAdmin(admin.ModelAdmin):
    list_display = [
        'produto', 'lote', 'data_validade', 'dias_para_vencer',
        'nivel_risco', 'resolvido',
    ]
    list_filter = ['nivel_risco', 'resolvido', 'filial']
    search_fields = ['produto__descricao', 'lote__numero_lote']
    readonly_fields = [
        'produto', 'lote', 'data_validade', 'dias_para_vencer', 'nivel_risco',
        'quantidade_em_risco', 'notificado_em', 'filial',
    ]


class ItemInventarioInline(admin.TabularInline):
    model = ItemInventario
    extra = 0
    readonly_fields = ['diferenca', 'valor_diferenca']


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    list_display = [
        'data_inicio', 'descricao', 'status', 'filial',
        'bloquear_movimentacoes', 'data_fim',
    ]
    list_filter = ['status', 'filial']
    search_fields = ['descricao']
    inlines = [ItemInventarioInline]
