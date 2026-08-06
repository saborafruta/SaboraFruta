"""
Rotas públicas do Cardápio Digital -- separadas do urls.py de staff de
propósito, pra que `/cardapio/...` seja trivial de auditar como "tudo que é
alcançável sem login".
"""
from django.urls import path

from .views import publico

app_name = 'food_service_publico'

urlpatterns = [
    path('<str:token>/', publico.CardapioView.as_view(), name='cardapio'),
    path('<str:token>/pedido/', publico.PedidoPendenteCreateView.as_view(), name='pedido-criar'),
    path('<str:token>/chamar-garcom/', publico.ChamarGarcomView.as_view(), name='chamar-garcom'),
    path('<str:token>/pedir-conta/', publico.PedirContaView.as_view(), name='pedir-conta'),
    path('<str:token>/avaliacao/<int:comanda_id>/', publico.AvaliacaoView.as_view(), name='avaliacao'),
]
