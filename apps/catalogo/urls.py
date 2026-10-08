from django.urls import path

from . import views

app_name = 'catalogo'

urlpatterns = [
    path('', views.CatalogoPainelView.as_view(), name='painel'),
    path('link-publico/', views.CatalogoLinkView.as_view(), name='link-publico'),
    path('pedidos/<int:pk>/<str:acao>/', views.PedidoAcaoView.as_view(), name='pedido-acao'),
]
