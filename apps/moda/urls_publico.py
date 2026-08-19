"""
Acesso público do cliente ao pedido — o link que vai no WhatsApp.

Fica FORA de `/moda/` de propósito. O `FilialMiddleware` barra `/moda/` para
quem não tem o módulo e exige filial ativa; o cliente não tem login nem
filial, e cairia no redirecionamento antes de chegar ao documento.

É uma URL-capacidade: quem tem o link vê o pedido, sem senha. É o mesmo
desenho do cardápio digital que já existe no sistema, e é o único jeito de
entregar o pedido por WhatsApp — o wa.me manda texto, não arquivo. Por isso
o token é opaco e longo, não o número do pedido: com sequencial, trocar um
dígito abriria o pedido do vizinho.

A página fica na raiz do token e o PDF em `pdf/`. Invertido, o cliente que
abrisse o link no celular receberia um download em vez de uma tela — e a
maior parte deles abre pelo WhatsApp, no celular.
"""
from django.urls import path

from . import views_publico

app_name = 'moda_publico'

urlpatterns = [
    # Nenhuma rota aqui aceita parâmetro além do token: não existe listagem,
    # busca nem paginação pública. O token é o escopo inteiro.
    path('<str:token>/', views_publico.PedidoOnlineView.as_view(), name='pedido'),
    path('<str:token>/pdf/', views_publico.PedidoPdfPublicoView.as_view(), name='pedido-pdf'),
    path('<str:token>/responder/', views_publico.PedidoResponderView.as_view(), name='pedido-responder'),
]
