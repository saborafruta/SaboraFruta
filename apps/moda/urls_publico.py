"""
Acesso público ao PDF do pedido — o link que vai no WhatsApp do cliente.

Fica FORA de `/moda/` de propósito. O `FilialMiddleware` barra `/moda/` para
quem não tem o módulo e exige filial ativa; o cliente não tem login nem
filial, e cairia no redirecionamento antes de chegar ao documento.

É uma URL-capacidade: quem tem o link vê o pedido, sem senha. É o mesmo
desenho do cardápio digital que já existe no sistema, e é o único jeito de
entregar o documento por WhatsApp — o wa.me manda texto, não arquivo. Por
isso o token é opaco e longo, não o número do pedido: com sequencial,
trocar um dígito abriria o pedido do vizinho.
"""
from django.urls import path

from . import views_publico

app_name = 'moda_publico'

urlpatterns = [
    path('<str:token>/', views_publico.PedidoPdfPublicoView.as_view(), name='pedido-pdf'),
]
