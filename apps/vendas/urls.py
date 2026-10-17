from django.urls import path

from apps.vendas import views

app_name = 'vendas'

urlpatterns = [
    path('', views.PedidoListView.as_view(), name='pedido-list'),
    path('dashboard/', views.DashboardForcaVendasView.as_view(), name='forca-vendas-dashboard'),
    path('externo/novo/', views.PedidoExternoNovoView.as_view(), name='pedido-externo-novo'),
    path('acompanhamento/', views.PedidoKanbanView.as_view(), name='pedido-kanban'),
    path('separacao/', views.PedidoSeparacaoFilaView.as_view(), name='pedido-separacao'),
    path('api/clientes/', views.ClienteForcaVendasBuscaView.as_view(), name='api-clientes'),
    path('api/produtos/', views.ProdutoForcaVendasBuscaView.as_view(), name='api-produtos'),
    path('<int:pk>/aprovar-externo/', views.AprovarPedidoExternoView.as_view(), name='pedido-aprovar-externo'),
    path('novo/', views.PedidoCreateView.as_view(), name='pedido-create'),
    path('<int:pk>/', views.PedidoDetailView.as_view(), name='pedido-detail'),
    path('<int:pk>/pdf/', views.PedidoVendaPdfView.as_view(), name='pedido-pdf'),
    path('<int:pk>/enviar-pdf/', views.EnviarPedidoVendaPdfWhatsAppView.as_view(), name='pedido-enviar-pdf'),
    path('<int:pk>/editar/', views.PedidoEditarView.as_view(), name='pedido-editar'),
    path('<int:pk>/itens/adicionar/', views.AdicionarItemView.as_view(), name='pedido-add-item'),
    path('<int:pk>/itens/<int:item_id>/remover/', views.RemoverItemView.as_view(), name='pedido-del-item'),
    path('<int:pk>/confirmar/', views.ConfirmarPedidoView.as_view(), name='pedido-confirmar'),
    path('<int:pk>/separar/', views.SepararPedidoView.as_view(), name='pedido-separar'),
    path('<int:pk>/faturar/', views.FaturarPedidoView.as_view(), name='pedido-faturar'),
    path('<int:pk>/iniciar-entrega/', views.IniciarEntregaPedidoView.as_view(), name='pedido-iniciar-entrega'),
    path('<int:pk>/concluir-entrega/', views.ConcluirEntregaPedidoView.as_view(), name='pedido-concluir-entrega'),
    path('<int:pk>/cancelar/', views.CancelarPedidoView.as_view(), name='pedido-cancelar'),
    path('<int:pk>/devolver/', views.DevolverPedidoView.as_view(), name='pedido-devolver'),
]
