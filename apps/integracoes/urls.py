from django.urls import path

from . import views


app_name = 'integracoes_api'

urlpatterns = [
    path('', views.RaizApiView.as_view(), name='raiz'),
    path('contexto/', views.ContextoApiView.as_view(), name='contexto'),
    path('filiais/', views.FiliaisApiView.as_view(), name='filiais'),
    path('clientes/', views.ClientesApiView.as_view(), name='clientes'),
    path('clientes/<int:pk>/', views.ClienteDetalheApiView.as_view(), name='cliente_detalhe'),
    path('produtos/', views.ProdutosApiView.as_view(), name='produtos'),
    path('produtos/<int:pk>/', views.ProdutoDetalheApiView.as_view(), name='produto_detalhe'),
    path('estoque/', views.EstoqueApiView.as_view(), name='estoque'),
    path('moda/produtos/', views.ProdutosModaApiView.as_view(), name='produtos_moda'),
    path('moda/produtos/<int:pk>/', views.ProdutoModaDetalheApiView.as_view(), name='produto_moda_detalhe'),
    path('moda/variantes/', views.VariantesModaApiView.as_view(), name='variantes_moda'),
    path('moda/ordens-producao/', views.OrdensProducaoApiView.as_view(), name='ordens_producao'),
    path('moda/ordens-producao/<int:pk>/', views.OrdemProducaoDetalheApiView.as_view(), name='ordem_producao_detalhe'),
]
