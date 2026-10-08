from django.urls import path

from .views_publico import (
    CatalogoPublicoView, ClienteCatalogoView, CupomCatalogoPublicoView,
    PedidoCatalogoConfirmarView,
)

app_name = 'catalogo_publico'

urlpatterns = [
    path('<str:token>/', CatalogoPublicoView.as_view(), name='catalogo'),
    path('<str:token>/cliente/', ClienteCatalogoView.as_view(), name='cliente'),
    path('<str:token>/cupom/', CupomCatalogoPublicoView.as_view(), name='cupom'),
    path(
        '<str:token>/pedido/<str:pedido_token>/confirmar/',
        PedidoCatalogoConfirmarView.as_view(),
        name='confirmar_pedido',
    ),
]
