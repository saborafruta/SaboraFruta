from django.urls import path

from apps.financeiro.views import financeiro
from apps.financeiro.views import receber as receber_views
from apps.financeiro.views import pagar as pagar_views
from apps.financeiro.views import plano_contas as pc_views
from apps.financeiro.views import credito_cliente as cc_views

app_name = "financeiro"

urlpatterns = [
    # ── Contas a Receber ──────────────────────────────────────────────────────
    path("receber/",                   receber_views.ContaReceberListView.as_view(),     name="receber_list"),
    path("receber/relatorio/",         receber_views.ContaReceberRelatorioView.as_view(), name="receber_relatorio"),
    path("receber/novo/",              receber_views.ContaReceberCreateView.as_view(),   name="receber_criar"),
    path("receber/<int:pk>/",          receber_views.ContaReceberDetailView.as_view(),   name="receber_detail"),
    path("receber/<int:pk>/baixar/",   receber_views.ContaReceberBaixaView.as_view(),    name="receber_baixar"),
    path("receber/<int:pk>/cancelar/", receber_views.ContaReceberCancelarView.as_view(), name="receber_cancelar"),

    # ── Contas a Pagar ────────────────────────────────────────────────────────
    path("pagar/",                    pagar_views.ContaPagarListView.as_view(),      name="pagar_list"),
    path("pagar/novo/",               pagar_views.ContaPagarCreateView.as_view(),    name="pagar_criar"),
    path("pagar/<int:pk>/",           pagar_views.ContaPagarDetailView.as_view(),    name="pagar_detail"),
    path("pagar/<int:pk>/pagar/",     pagar_views.ContaPagarPagamentoView.as_view(), name="pagar_pagar"),
    path("pagar/<int:pk>/cancelar/",  pagar_views.ContaPagarCancelarView.as_view(),  name="pagar_cancelar"),

    # ── Plano de Contas ───────────────────────────────────────────────────────
    path("plano-contas/",                       pc_views.PlanoContasListView.as_view(),        name="plano_contas_list"),
    path("plano-contas/novo/",                  pc_views.PlanoContasCreateView.as_view(),      name="plano_contas_criar"),
    path("plano-contas/<int:pk>/editar/",       pc_views.PlanoContasEditView.as_view(),        name="plano_contas_editar"),
    path("plano-contas/<int:pk>/toggle-ativo/", pc_views.PlanoContasToggleAtivoView.as_view(), name="plano_contas_toggle"),

    # ── Créditos de Clientes ──────────────────────────────────────────────────
    path("creditos/",              cc_views.CreditoClienteListView.as_view(),   name="credito_list"),
    path("creditos/novo/",         cc_views.CreditoClienteCreateView.as_view(), name="credito_criar"),
    path("creditos/<int:pk>/",        cc_views.CreditoClienteDetailView.as_view(), name="credito_detail"),
    path("creditos/<int:pk>/editar/", cc_views.CreditoClienteEditView.as_view(),   name="credito_editar"),
    path("api/credito-saldo/",     cc_views.api_credito_saldo,                  name="api_credito_saldo"),

    # ── Outros ───────────────────────────────────────────────────────────────
    path("documentos/",       financeiro.documentos_fiscais_list, name="documentos"),
    path("dre/",              financeiro.dre_view,               name="dre"),
    path("formas-pagamento/", financeiro.formas_pagamento,       name="formas_pagamento"),
    path("api/formas-pagamento/<int:pk>/taxas/", financeiro.api_taxas_forma_pagamento, name="api_taxas_forma"),
]
