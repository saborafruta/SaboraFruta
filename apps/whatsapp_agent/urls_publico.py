from django.urls import path

from .views_publico import OrcamentoPublicoView


app_name = 'orcamento_publico'

urlpatterns = [
    path('<str:token>/', OrcamentoPublicoView.as_view(), name='detalhe'),
]
