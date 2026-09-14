"""
URLs da API REST de equalização de estoque (Fase 31).

Autenticação: sessão do próprio ERP (cookie de login), não JWT. O
projeto já tem `rest_framework_simplejwt` configurado como classe de
autenticação padrão do DRF, mas nenhum endpoint de token foi conectado
em lugar nenhum do sistema -- e não daria pra conectar um genérico
aqui: nesta arquitetura cada empresa tem seu próprio banco, então
autenticar por usuário/senha exige saber ANTES em qual banco checar as
credenciais, exatamente o problema que o login por sessão já resolve
hoje (ver `apps/core/services/request_scope.py`). Construir esse fluxo
de token corretamente é uma peça de autenticação nova e sensível de
segurança, fora do escopo desta entrega -- decisão confirmada com o
usuário. Quando o ERP tiver um login JWT tenant-aware, basta trocar
`authentication_classes` nas views.
"""
from django.urls import path

from . import views

app_name = "equalizacao_api"

urlpatterns = [
    path("", views.EqualizacaoStatusView.as_view(), name="status"),
    path("recomendacoes/", views.EqualizacaoRecomendacoesView.as_view(), name="recomendacoes"),
    path("dashboard/", views.EqualizacaoDashboardView.as_view(), name="dashboard"),
    path("analisar/", views.EqualizacaoAnalisarView.as_view(), name="analisar"),
    path("simular/", views.EqualizacaoSimularView.as_view(), name="simular"),
    path("aprovar/", views.EqualizacaoAprovarView.as_view(), name="aprovar"),
    path("rejeitar/", views.EqualizacaoRejeitarView.as_view(), name="rejeitar"),
    path("transferir/", views.EqualizacaoTransferirView.as_view(), name="transferir"),
    path("historico/", views.EqualizacaoHistoricoView.as_view(), name="historico"),
    path("indicadores/", views.EqualizacaoIndicadoresView.as_view(), name="indicadores"),
]
