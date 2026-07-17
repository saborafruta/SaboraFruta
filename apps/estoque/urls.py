from django.urls import path

from apps.estoque import views

app_name = 'estoque'

urlpatterns = [
    # Consulta de estoque
    path('', views.EstoqueListView.as_view(), name='estoque-list'),
    path('produtos/<int:pk>/inline-edit/', views.EstoqueInlineEditView.as_view(), name='estoque-inline-edit'),
    path('produtos/<int:pk>/extrato/', views.EstoqueKardexProdutoView.as_view(), name='estoque-kardex-produto'),
    path('custos-entrada/', views.EntradaCustoEstoqueListView.as_view(), name='entrada-custos-list'),
    path('relatorios/', views.RelatorioEstoqueView.as_view(), name='relatorio-list'),
    path('reposicao/', views.ReposicaoListView.as_view(), name='reposicao-list'),
    path('sugestao-compras/', views.SugestaoComprasView.as_view(), name='sugestao-compras'),
    path('movimentacoes/', views.MovimentacaoListView.as_view(), name='movimentacao-list'),

    # Operacoes
    path('movimentacoes/nova/', views.MovimentacaoManualView.as_view(), name='movimentacao-create'),
    path('ajuste/', views.AjusteEstoqueView.as_view(), name='ajuste'),
    path('transferencia/', views.TransferenciaView.as_view(), name='transferencia'),

    # Outras Movimentacoes
    path('outras-movimentacoes/', views.OutrasMovimentacoesHubView.as_view(), name='outras-mov-hub'),
    path('outras-movimentacoes/devolucao/', views.DevolucaoClienteView.as_view(), name='devolucao-cliente'),
    path('outras-movimentacoes/devolucao-fornecedor/', views.DevolucaoFornecedorView.as_view(), name='devolucao-fornecedor'),
    path('outras-movimentacoes/saida-especial/', views.SaidaEspecialView.as_view(), name='saida-especial'),
    path('outras-movimentacoes/transferencia-lojas/', views.TransferenciaLojaView.as_view(), name='transferencia-lojas'),
    path('outras-movimentacoes/transferencia-lojas/api/', views.TransferenciaLojaApiView.as_view(), name='transferencia-lojas-api'),
    path('outras-movimentacoes/devolucao/api/', views.DevolucaoClienteApiView.as_view(), name='devolucao-cliente-api'),
    path('outras-movimentacoes/devolucao/venda/', views.VendaDevolucaoJsonView.as_view(), name='devolucao-venda-json'),

    # Inventario
    path('inventarios/', views.InventarioListView.as_view(), name='inventario-list'),
    path('inventarios/novo/', views.InventarioCreateView.as_view(), name='inventario-create'),
    path('inventarios/<int:pk>/', views.InventarioDetailView.as_view(), name='inventario-detail'),
    path('inventarios/<int:pk>/divergencias/', views.InventarioDivergenciasView.as_view(), name='inventario-divergencias'),
    path('inventarios/<int:pk>/cancelar/', views.InventarioCancelView.as_view(), name='inventario-cancel'),

    # Lotes
    path('lotes/', views.LoteListView.as_view(), name='lote-list'),
    path('lotes/novo/', views.LoteCreateView.as_view(), name='lote-create'),
    path('lotes/<int:pk>/', views.LoteUpdateView.as_view(), name='lote-update'),
    path('lotes/<int:pk>/baixa-validade/', views.LoteBaixaValidadeView.as_view(), name='lote-baixa-validade'),

    # Alertas
    path('alertas/', views.AlertaListView.as_view(), name='alerta-list'),

    # Endpoints JSON para typeahead
    path('api/fornecedores/buscar/', views.FornecedorSearchJsonView.as_view(), name='fornecedor-search-json'),
    path('api/produtos/buscar/', views.ProdutoEstoqueSearchJsonView.as_view(), name='produto-estoque-search-json'),
    path('api/clientes/buscar/', views.ClienteSearchJsonView.as_view(), name='cliente-search-json'),
    path('api/lotes/buscar/', views.LoteSearchJsonView.as_view(), name='lote-search-json'),
]
