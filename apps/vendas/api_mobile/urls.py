from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views
from .throttling import MobileLoginThrottle


app_name = 'forca_vendas_mobile_api'

urlpatterns = [
    path(
        'token/',
        TokenObtainPairView.as_view(throttle_classes=[MobileLoginThrottle]),
        name='token',
    ),
    path(
        'token/refresh/',
        TokenRefreshView.as_view(throttle_classes=[MobileLoginThrottle]),
        name='token_refresh',
    ),
    path('contexto/', views.ContextoMobileView.as_view(), name='contexto'),
    path('clientes/', views.ClientesMobileView.as_view(), name='clientes'),
    path('produtos/', views.ProdutosMobileView.as_view(), name='produtos'),
    path('opcoes-pedido/', views.OpcoesPedidoMobileView.as_view(), name='opcoes_pedido'),
    path('pedidos/', views.PedidosMobileView.as_view(), name='pedidos'),
    path('separacoes/', views.SeparacoesMobileView.as_view(), name='separacoes'),
    path(
        'separacoes/<int:pedido_id>/concluir/',
        views.ConcluirSeparacaoMobileView.as_view(),
        name='concluir_separacao',
    ),
]
