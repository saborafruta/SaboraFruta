"""
O endereço que vai dentro do QR Code.

`/q/<codigo>/` — curto de propósito. Cada caractere a menos é uma linha a
menos no desenho, e um QR menos denso é lido de mais longe, com a câmera
suja e a luz fraca que o chão de fábrica tem. `/moda/producao/qr/<codigo>/`
faria o mesmo trabalho com o dobro dos módulos.

Fica fora de `/moda/` também porque o `FilialMiddleware` barra `/moda/`
antes da view — e quem escaneia pode estar deslogado, ou com outra filial
ativa. Essas duas situações precisam de resposta própria (voltar ao código
depois do login, avisar que é outra filial), não do redirecionamento
genérico do middleware.

Autorização não mora aqui: a view só descobre que documento é e redireciona
para a tela dele, que exige login, permissão e filial como sempre.
"""
from django.urls import path

from . import views_qr

app_name = 'qr'

urlpatterns = [
    path('<str:codigo>/', views_qr.QrAbrirView.as_view(), name='abrir'),
]
