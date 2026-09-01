from django.urls import path
from apps.analytics.views import dashboards

app_name = "analytics"

urlpatterns = [
    path("operacional/", dashboards.dashboard_operacional, name="operacional"),
    path("comercial/", dashboards.dashboard_comercial, name="comercial"),
    path("producao/", dashboards.dashboard_producao, name="producao"),
    path("dre/", dashboards.dashboard_dre, name="dre"),
    path("vendas/", dashboards.historico_vendas, name="vendas"),
    path("vendas/relatorio/", dashboards.historico_vendas_relatorio, name="vendas-relatorio"),
    path("vendas/<int:pk>/financeiro/", dashboards.historico_venda_financeiro, name="venda-financeiro"),
]
