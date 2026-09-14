from django.contrib import admin

from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, ClasseFiscal, ClasseFiscalAliquota, ClasseFiscalFilial,
    ItemTabelaPreco, MarcaProduto, MarcaProdutoFilial, NaturezaOperacao, NaturezaOperacaoFilial, Produto,
    ProdutoApresentacao, ProdutoApresentacaoFilial, ProdutoFilial,
    TabelaPreco, TabelaPrecoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


@admin.register(CategoriaProduto)
class CategoriaProdutoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'categoria_pai', 'empresa', 'filial', 'ativo']
    list_filter = ['ativo', 'empresa', 'filial']
    search_fields = ['nome']
    autocomplete_fields = ['categoria_pai']


@admin.register(CategoriaProdutoFilial)
class CategoriaProdutoFilialAdmin(admin.ModelAdmin):
    list_display = ['categoria', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['categoria__nome']


@admin.register(UnidadeMedida)
class UnidadeMedidaAdmin(admin.ModelAdmin):
    list_display = ['sigla', 'descricao', 'tipo', 'casas_decimais', 'empresa', 'ativo']
    list_filter = ['ativo', 'tipo', 'empresa']
    search_fields = ['sigla', 'descricao']


@admin.register(UnidadeMedidaFilial)
class UnidadeMedidaFilialAdmin(admin.ModelAdmin):
    list_display = ['unidade', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['unidade__sigla', 'unidade__descricao']


@admin.register(MarcaProduto)
class MarcaProdutoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'empresa', 'filial', 'ativo']
    list_filter = ['ativo', 'empresa', 'filial']
    search_fields = ['nome', 'descricao']


@admin.register(MarcaProdutoFilial)
class MarcaProdutoFilialAdmin(admin.ModelAdmin):
    list_display = ['marca', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['marca__nome']


class ClasseFiscalAliquotaInline(admin.TabularInline):
    model = ClasseFiscalAliquota
    extra = 0
    fields = ['uf_destino', 'icms_interno', 'icms_interestadual', 'tem_st',
              'icms_st', 'fcp', 'ipi', 'pis', 'cofins', 'ibs', 'cbs',
              'vigencia_inicio', 'vigencia_fim']


@admin.register(ClasseFiscal)
class ClasseFiscalAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'descricao', 'empresa', 'ativo']
    list_filter = ['ativo', 'empresa']
    search_fields = ['codigo', 'descricao']
    inlines = [ClasseFiscalAliquotaInline]


@admin.register(ClasseFiscalFilial)
class ClasseFiscalFilialAdmin(admin.ModelAdmin):
    list_display = ['classe_fiscal', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['classe_fiscal__codigo', 'classe_fiscal__descricao']


@admin.register(NaturezaOperacao)
class NaturezaOperacaoAdmin(admin.ModelAdmin):
    list_display = ['descricao', 'tipo', 'cfop_dentro_estado', 'cfop_fora_estado', 'ativo']
    list_filter = ['ativo', 'tipo', 'empresa']
    search_fields = ['descricao', 'cfop_dentro_estado']


@admin.register(NaturezaOperacaoFilial)
class NaturezaOperacaoFilialAdmin(admin.ModelAdmin):
    list_display = ['natureza', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['natureza__descricao', 'natureza__cfop_dentro_estado']


class ProdutoApresentacaoInline(admin.TabularInline):
    model = ProdutoApresentacao
    extra = 0
    autocomplete_fields = ['unidade']
    fields = [
        'descricao', 'unidade', 'fator_conversao', 'preco_venda', 'preco_minimo', 'codigo',
        'permite_venda', 'permite_compra', 'permite_estoque',
        'principal_venda', 'principal_compra', 'ativo',
    ]


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = [
        'codigo', 'descricao', 'categoria', 'subcategoria', 'marca', 'fornecedor', 'tipo_produto', 'ncm',
        'preco_venda', 'preco_custo_medio', 'ativo',
    ]
    list_filter = ['ativo', 'tipo_produto', 'categoria', 'marca', 'fornecedor', 'controla_lote', 'filial']
    search_fields = ['codigo', 'codigo_barras', 'descricao', 'ncm']
    readonly_fields = ['preco_custo_medio', 'margem_lucro']
    autocomplete_fields = ['filial', 'categoria', 'subcategoria', 'marca', 'fornecedor', 'unidade_medida', 'classe_fiscal']
    inlines = [ProdutoApresentacaoInline]
    fieldsets = (
        ('Identificação', {
            'fields': ('filial', 'codigo', 'codigo_barras', 'codigos_barras_extras',
                       'descricao', 'descricao_completa', 'descricao_pdv',
                       'categoria', 'subcategoria', 'marca', 'fornecedor', 'unidade_medida', 'unidade_medida_compra',
                       'fator_conversao_compra', 'tipo_produto', 'foto_url', 'ativo'),
        }),
        ('Fiscal', {
            'fields': ('ncm', 'cest', 'cfop_venda_interna', 'cfop_venda_interestadual',
                       'cfop_venda_exportacao', 'cfop_devolucao', 'cfop_compra',
                       'origem_produto', 'classe_fiscal', 'codigo_enquadramento_ipi'),
        }),
        ('Preços', {
            'fields': ('preco_custo', 'preco_custo_medio', 'preco_venda', 'margem_lucro'),
        }),
        ('Estoque', {
            'fields': ('estoque_minimo', 'estoque_maximo', 'ponto_reposicao',
                       'controla_lote', 'controla_validade', 'dias_aviso_vencimento',
                       'saida_fefo', 'permite_venda_sem_estoque', 'localizacao_estoque'),
        }),
        ('Granel', {
            'fields': ('codigo_balanca', 'tara_padrao', 'variacao_peso_permitida', 'fracionavel'),
            'classes': ('collapse',),
        }),
        ('Físico', {
            'fields': ('peso_bruto', 'peso_liquido', 'largura', 'altura', 'profundidade'),
            'classes': ('collapse',),
        }),
    )


@admin.register(ProdutoFilial)
class ProdutoFilialAdmin(admin.ModelAdmin):
    list_display = ['produto', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['produto__descricao', 'produto__codigo', 'produto__codigo_barras']


@admin.register(ProdutoApresentacao)
class ProdutoApresentacaoAdmin(admin.ModelAdmin):
    list_display = [
        'produto', 'descricao', 'unidade', 'fator_conversao', 'preco_venda',
        'principal_venda', 'principal_compra', 'ativo',
    ]
    list_filter = ['ativo', 'principal_venda', 'principal_compra', 'permite_venda', 'permite_compra', 'unidade']
    search_fields = ['produto__descricao', 'produto__codigo', 'descricao', 'codigo']
    autocomplete_fields = ['produto', 'unidade']
    fieldsets = (
        (None, {
            'fields': (
                'produto', 'unidade', 'descricao', 'codigo', 'fator_conversao', 'ativo',
            ),
        }),
        ('Preços', {
            'fields': ('preco_venda', 'preco_minimo'),
        }),
        ('Uso', {
            'fields': (
                'permite_venda', 'permite_compra', 'permite_estoque',
                'principal_venda', 'principal_compra',
            ),
        }),
        ('Físico', {
            'fields': ('peso_bruto', 'peso_liquido', 'largura', 'altura', 'profundidade'),
            'classes': ('collapse',),
        }),
    )


@admin.register(ProdutoApresentacaoFilial)
class ProdutoApresentacaoFilialAdmin(admin.ModelAdmin):
    list_display = ['apresentacao', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['apresentacao__descricao', 'apresentacao__produto__descricao', 'apresentacao__produto__codigo']
    autocomplete_fields = ['apresentacao', 'filial']


class ItemTabelaPrecoInline(admin.TabularInline):
    model = ItemTabelaPreco
    extra = 0
    autocomplete_fields = ['produto', 'apresentacao']


@admin.register(TabelaPreco)
class TabelaPrecoAdmin(admin.ModelAdmin):
    list_display = ['descricao', 'tipo', 'filial', 'data_inicio', 'data_fim', 'ativo']
    list_filter = ['ativo', 'tipo', 'filial']
    search_fields = ['descricao']
    inlines = [ItemTabelaPrecoInline]


@admin.register(TabelaPrecoFilial)
class TabelaPrecoFilialAdmin(admin.ModelAdmin):
    list_display = ['tabela', 'filial', 'ativo']
    list_filter = ['ativo', 'filial']
    search_fields = ['tabela__descricao']
