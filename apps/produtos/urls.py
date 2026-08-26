from django.urls import path

from apps.produtos import views

app_name = 'produtos'

urlpatterns = [
    # Produtos
    path('', views.ProdutoListView.as_view(), name='produto-list'),
    path('fiscal/', views.ProdutoFiscalListView.as_view(), name='produto-fiscal-list'),
    path('exportar/csv/', views.ProdutoExportCsvView.as_view(), name='produto-export-csv'),
    path('exportar/pdf/', views.ProdutoExportPdfView.as_view(), name='produto-export-pdf'),
    path('exportar/todos/csv/', views.ProdutoExportTodosCsvView.as_view(), name='produto-export-todos-csv'),
    path('combos-promocoes/', views.ComboPromocaoListView.as_view(), name='combo-promocao-list'),
    path('atualizacao-precos/', views.AtualizacaoPrecoView.as_view(), name='atualizacao-precos'),
    path('combos-promocoes/produtos/buscar/', views.ProdutoPromocaoSearchView.as_view(), name='combo-promocao-produto-search'),
    path('combos-promocoes/log/registros/', views.ComboPromocaoLogItemsView.as_view(), name='combo-promocao-log-items'),
    path('combos-promocoes/log/exportar/csv/', views.ComboPromocaoLogExportCsvView.as_view(), name='combo-promocao-log-export-csv'),
    path('combos-promocoes/log/exportar/pdf/', views.ComboPromocaoLogExportPdfView.as_view(), name='combo-promocao-log-export-pdf'),
    path('novo/', views.ProdutoCreateView.as_view(), name='produto-create'),
    path('<int:pk>/duplicar/', views.ProdutoDuplicarView.as_view(), name='produto-duplicar'),
    path(
        '<int:pk>/vinculos-fornecedor/<int:vinculo_pk>/remover/',
        views.ProdutoFornecedorVinculoDeleteView.as_view(),
        name='produto-fornecedor-vinculo-delete',
    ),
    path('<int:pk>/', views.ProdutoUpdateView.as_view(), name='produto-update'),
    path('<int:pk>/log/exportar/csv/', views.ProdutoLogExportCsvView.as_view(), name='produto-log-export-csv'),
    path('<int:pk>/log/exportar/pdf/', views.ProdutoLogExportPdfView.as_view(), name='produto-log-export-pdf'),
    path('<int:pk>/log/registros/', views.ProdutoLogItemsView.as_view(), name='produto-log-items'),
    path('<int:pk>/inline-editar/', views.ProdutoInlineEditView.as_view(), name='produto-inline-edit'),
    path('<int:pk>/foto/', views.ProdutoImagemView.as_view(), name='produto-image-file'),
    path('<int:pk>/imagem/', views.ProdutoImagemUpdateView.as_view(), name='produto-image-update'),
    path('<int:pk>/excluir/', views.ProdutoDeleteView.as_view(), name='produto-delete'),
    path('<int:pk>/toggle-ativo/', views.ProdutoToggleAtivoView.as_view(), name='produto-toggle-ativo'),

    # Categorias
    path('categorias/', views.CategoriaListView.as_view(), name='categoria-list'),
    path('categorias/novo/', views.CategoriaCreateView.as_view(), name='categoria-create'),
    path('categorias/<int:pk>/', views.CategoriaUpdateView.as_view(), name='categoria-update'),

    # Marcas / Fabricantes
    path('marcas/', views.MarcaListView.as_view(), name='marca-list'),
    path('marcas/novo/', views.MarcaCreateView.as_view(), name='marca-create'),
    path('marcas/<int:pk>/', views.MarcaUpdateView.as_view(), name='marca-update'),

    # Unidades
    path('unidades/', views.UnidadeListView.as_view(), name='unidade-list'),
    path('unidades/novo/', views.UnidadeCreateView.as_view(), name='unidade-create'),
    path('unidades/<int:pk>/', views.UnidadeUpdateView.as_view(), name='unidade-update'),

    # Tabelas de Preço
    path('tabelas-preco/', views.TabelaPrecoListView.as_view(), name='tabela-list'),
    path('tabelas-preco/novo/', views.TabelaPrecoCreateView.as_view(), name='tabela-create'),
    path('tabelas-preco/produtos/buscar/', views.ProdutoSearchTabelaNovaView.as_view(), name='tabela-nova-produto-search'),
    path('tabelas-preco/<int:pk>/', views.TabelaPrecoUpdateView.as_view(), name='tabela-update'),
    path('tabelas-preco/<int:pk>/toggle-ativo/', views.TabelaPrecoToggleAtivoView.as_view(), name='tabela-toggle-ativo'),
    path('tabelas-preco/<int:tabela_pk>/itens/adicionar/', views.ItemTabelaPrecoCreateView.as_view(), name='tabela-item-create'),
    path('tabelas-preco/<int:tabela_pk>/itens/<int:item_pk>/remover/', views.ItemTabelaPrecoDeleteView.as_view(), name='tabela-item-delete'),
    path('tabelas-preco/<int:tabela_pk>/produtos/buscar/', views.ProdutoSearchParaTabelaView.as_view(), name='tabela-produto-search'),
]
