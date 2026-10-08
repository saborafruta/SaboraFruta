from django.urls import path

from . import views

app_name = 'catalogo'

urlpatterns = [
    path('', views.CatalogoPainelView.as_view(), name='painel'),
    path('pedidos/', views.PedidosCatalogoView.as_view(), name='pedidos'),
    path('pedidos/<int:pk>/imprimir/', views.PedidoImpressaoView.as_view(), name='pedido-imprimir'),
    path('pedidos/<int:pk>/mover/', views.PedidoMoverView.as_view(), name='pedido-mover'),
    path('link-publico/', views.CatalogoLinkView.as_view(), name='link-publico'),
    path('cupons/novo/', views.CupomCatalogoCriarView.as_view(), name='cupom-criar'),
    path('cupons/<int:pk>/alternar/', views.CupomCatalogoAlternarView.as_view(), name='cupom-alternar'),
    path('pedidos/<int:pk>/<str:acao>/', views.PedidoAcaoView.as_view(), name='pedido-acao'),
]
