from django.urls import path

from apps.compras import views

app_name = 'compras'

urlpatterns = [
    # Pedidos de compra
    path('', views.PedidoCompraListView.as_view(), name='pedido-list'),
    path('novo/', views.PedidoCompraCreateView.as_view(), name='pedido-create'),
    path('<int:pk>/', views.PedidoCompraDetailView.as_view(), name='pedido-detail'),
    path('<int:pk>/itens/adicionar/', views.AdicionarItemCompraView.as_view(), name='pedido-add-item'),
    path('<int:pk>/itens/<int:item_id>/remover/', views.RemoverItemCompraView.as_view(), name='pedido-del-item'),
    path('<int:pk>/aprovar/', views.AprovarPedidoCompraView.as_view(), name='pedido-aprovar'),
    path('<int:pk>/enviar/', views.EnviarPedidoCompraView.as_view(), name='pedido-enviar'),
    path('<int:pk>/cancelar/', views.CancelarPedidoCompraView.as_view(), name='pedido-cancelar'),

    # Entradas de NF
    path('entradas/', views.EntradaNFListView.as_view(), name='entrada-list'),
    path('entradas/localizar/', views.EntradaNFLocalizarNotaView.as_view(), name='entrada-localizar'),
    path('entradas/importar-xml/', views.EntradaNFImportarXMLView.as_view(), name='entrada-importar-xml'),
    path('entradas/consultar-chave/', views.EntradaNFConsultarChaveView.as_view(), name='entrada-consultar-chave'),
    path('entradas/nova/', views.EntradaNFCreateView.as_view(), name='entrada-create'),
    path('entradas/<int:pk>/', views.EntradaNFDetailView.as_view(), name='entrada-detail'),
    path('entradas/<int:pk>/conferencia/', views.EntradaNFConferenciaView.as_view(), name='entrada-conferencia'),
    path('entradas/<int:pk>/fornecedor-pendente/', views.EntradaNFFornecedorPendenteView.as_view(), name='entrada-fornecedor-pendente'),
    path('entradas/<int:pk>/diferencas/', views.EntradaNFDiferencasView.as_view(), name='entrada-diferencas'),
    path('entradas/<int:pk>/financeiro/', views.EntradaNFFinanceiroView.as_view(), name='entrada-financeiro'),
    path('entradas/<int:pk>/financeiro/gerar-contas-pagar/', views.EntradaNFGerarContasPagarView.as_view(), name='entrada-gerar-contas-pagar'),
    path('entradas/<int:pk>/custos/', views.EntradaNFCustosView.as_view(), name='entrada-custos'),
    path('entradas/<int:pk>/finalizacao/', views.EntradaNFFinalizacaoView.as_view(), name='entrada-finalizacao'),
    path('entradas/<int:pk>/itens/adicionar/', views.AdicionarItemEntradaView.as_view(), name='entrada-add-item'),
    path('entradas/<int:pk>/itens/<int:item_id>/remover/', views.RemoverItemEntradaView.as_view(), name='entrada-del-item'),
    path('entradas/<int:pk>/itens/<int:item_id>/vincular/', views.EntradaNFVincularItemView.as_view(), name='entrada-vincular-item'),
    path('entradas/<int:pk>/itens/<int:item_id>/desvincular/', views.EntradaNFDesvincularItemView.as_view(), name='entrada-desvincular-item'),
    path('entradas/<int:pk>/itens/reprocessar-vinculos/', views.EntradaNFReprocessarVinculosView.as_view(), name='entrada-reprocessar-vinculos'),
    path('entradas/<int:pk>/itens/vincular-sugestoes/', views.EntradaNFVincularSugestoesView.as_view(), name='entrada-vincular-sugestoes'),
    path('entradas/<int:pk>/itens/<int:item_id>/criar-produto/', views.EntradaNFCriarProdutoItemView.as_view(), name='entrada-criar-produto-item'),
    path('entradas/<int:pk>/efetivar/', views.EfetivarEntradaView.as_view(), name='entrada-efetivar'),
    path('entradas/<int:pk>/estorno/', views.EstornarEntradaView.as_view(), name='entrada-estorno'),
    path('entradas/<int:pk>/cancelar/', views.CancelarEntradaView.as_view(), name='entrada-cancelar'),
]
