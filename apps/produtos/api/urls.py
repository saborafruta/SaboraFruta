"""
URLs da API REST de cadastro de produtos e apresentacoes.

Ver docstring de `apps/produtos/api/views.py` para a decisao de montar
isto em `/api/produtos/` em vez de `/api/v1/` (prefixo ja usado pela API
de integracoes, com outro proposito e outro esquema de autenticacao).
"""
from django.urls import path

from . import views

app_name = 'produtos_api'

urlpatterns = [
    path('produtos/', views.ProdutosView.as_view(), name='produtos'),
    path('produtos/<int:pk>/', views.ProdutoDetalheView.as_view(), name='produto_detalhe'),
    path('produtos/<int:pk>/apresentacoes/', views.ProdutoApresentacoesView.as_view(), name='produto_apresentacoes'),
    path('apresentacoes/<int:pk>/', views.ApresentacaoDetalheView.as_view(), name='apresentacao_detalhe'),
]
