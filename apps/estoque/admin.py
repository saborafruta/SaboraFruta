from django.contrib import admin

from apps.estoque.models import (
    AlertaVencimento, ConferenciaTransferencia, ConfiguracaoAbcEstoque,
    ConfiguracaoDemandaPonderada, Deposito, Estoque, FaixaCoberturaEstoque,
    Inventario, ItemInventario, LoteProduto, MovimentacaoEstoque,
    NivelAprovacaoTransferencia, SolicitacaoTransferencia,
    SugestaoEqualizacaoSnapshot,
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


@admin.register(ConferenciaTransferencia)
class ConferenciaTransferenciaAdmin(admin.ModelAdmin):
    list_display = ['documento_numero', 'filial_origem', 'filial_destino', 'status', 'etapa', 'created_at']
    list_filter = ['status', 'etapa', 'filial_origem', 'filial_destino']
    search_fields = ['documento_numero']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(FaixaCoberturaEstoque)
class FaixaCoberturaEstoqueAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'empresa', 'categoria', 'produto', 'dias_critico', 'dias_baixo', 'dias_normal', 'dias_alto']
    list_filter = ['empresa']
    search_fields = ['produto__descricao', 'categoria__nome']
    autocomplete_fields = ['produto', 'categoria']


@admin.register(ConfiguracaoAbcEstoque)
class ConfiguracaoAbcEstoqueAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'classe', 'multiplicador_minimo', 'multiplicador_maximo', 'dias_cobertura_extra']
    list_filter = ['empresa', 'classe']


@admin.register(ConfiguracaoDemandaPonderada)
class ConfiguracaoDemandaPonderadaAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'peso_7_dias', 'peso_15_dias', 'peso_30_dias', 'peso_60_dias', 'peso_90_dias']


@admin.register(NivelAprovacaoTransferencia)
class NivelAprovacaoTransferenciaAdmin(admin.ModelAdmin):
    list_display = ['nivel_nome', 'empresa', 'valor_minimo', 'valor_maximo']
    list_filter = ['empresa']


@admin.register(SolicitacaoTransferencia)
class SolicitacaoTransferenciaAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'produto', 'filial_origem', 'filial_destino', 'quantidade',
        'valor_estimado', 'status', 'solicitante', 'aprovador', 'created_at',
    ]
    list_filter = ['status', 'filial_origem', 'filial_destino']
    search_fields = ['produto__descricao', 'documento_numero']
    readonly_fields = ['created_at', 'updated_at']

    def has_add_permission(self, request):
        return False  # Só é criada via fluxo de aprovação (apps.estoque.services.aprovacao_transferencia)

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SugestaoEqualizacaoSnapshot)
class SugestaoEqualizacaoSnapshotAdmin(admin.ModelAdmin):
    list_display = ['produto', 'filial_origem', 'filial_destino', 'quantidade_sugerida', 'score', 'created_at']
    list_filter = ['empresa', 'filial_origem', 'filial_destino']
    search_fields = ['produto__descricao']
    readonly_fields = [f.name for f in SugestaoEqualizacaoSnapshot._meta.fields]

    def has_add_permission(self, request):
        return False  # Só é gerada pela rotina de automação (apps.estoque.tasks.equalizacao)

    def has_change_permission(self, request, obj=None):
        return False
