from django.urls import path
from django.views.generic import RedirectView

from apps.financeiro.views import financeiro
from apps.financeiro.views import receber as receber_views
from apps.financeiro.views import pagar as pagar_views
from apps.financeiro.views import plano_contas as pc_views
from apps.financeiro.views import plano_contabil as plano_contabil_views
from apps.financeiro.views.contas_bancarias import ContaBancariaListView
from apps.financeiro.views import credito_cliente as cc_views
from apps.financeiro.views.painel import PainelFinanceiroView
from apps.financeiro.views.fluxo_caixa import FluxoCaixaView
from apps.financeiro.views.conciliacao import (
    ExtratoListView, ExtratoLancarView, ExtratoConciliarView, ExtratoDesconciliarView,
)

app_name = "financeiro"

urlpatterns = [
    # ── Gestão Financeira ───────────────────────────────────────────────────────
    path("painel/",       PainelFinanceiroView.as_view(), name="painel"),
    path("fluxo-caixa/",  FluxoCaixaView.as_view(),       name="fluxo_caixa"),
    path("contas-bancarias/", ContaBancariaListView.as_view(), name="contas_bancarias"),

    # ── Conciliação Bancária ─────────────────────────────────────────────────────
    path("conciliacao/",                    ExtratoListView.as_view(),         name="conciliacao_list"),
    path("conciliacao/lancar/",             ExtratoLancarView.as_view(),       name="conciliacao_lancar"),
    path("conciliacao/<int:pk>/conciliar/", ExtratoConciliarView.as_view(),    name="conciliacao_conciliar"),
    path("conciliacao/<int:pk>/desconciliar/", ExtratoDesconciliarView.as_view(), name="conciliacao_desconciliar"),

    # ── Contas a Receber ──────────────────────────────────────────────────────
    path("receber/",                   receber_views.ContaReceberListView.as_view(),     name="receber_list"),
    path("receber/relatorio/",         receber_views.ContaReceberRelatorioView.as_view(), name="receber_relatorio"),
    path("receber/novo/",              receber_views.ContaReceberCreateView.as_view(),   name="receber_criar"),
    path("receber/<int:pk>/",          receber_views.ContaReceberDetailView.as_view(),   name="receber_detail"),
    path("receber/<int:pk>/baixar/",   receber_views.ContaReceberBaixaView.as_view(),    name="receber_baixar"),
    path("receber/<int:pk>/cancelar/", receber_views.ContaReceberCancelarView.as_view(), name="receber_cancelar"),
    path("receber/<int:pk>/editar-prazo/", receber_views.ContaReceberEditarPrazoView.as_view(), name="receber_editar_prazo"),

    # ── Contas a Pagar ────────────────────────────────────────────────────────
    path("pagar/",                    pagar_views.ContaPagarListView.as_view(),      name="pagar_list"),
    path("pagar/relatorio/",          pagar_views.ContaPagarRelatorioView.as_view(), name="pagar_relatorio"),
    path("pagar/pagas/",              pagar_views.ContaPagaListView.as_view(),       name="pagar_pagas"),
    path("pagar/pagas/relatorio/",    pagar_views.ContaPagaRelatorioView.as_view(),  name="pagar_pagas_relatorio"),
    path("pagar/novo/",               pagar_views.ContaPagarCreateView.as_view(),    name="pagar_criar"),
    path("pagar/nfe/consultar/",       pagar_views.ContaPagarNotaFiscalLookupView.as_view(), name="pagar_nfe_consultar"),
    path("pagar/<int:pk>/",           pagar_views.ContaPagarDetailView.as_view(),    name="pagar_detail"),
    path("pagar/<int:pk>/pagar/",     pagar_views.ContaPagarPagamentoView.as_view(), name="pagar_pagar"),
    path(
        "pagar/<int:pk>/pagamentos/<int:pagamento_pk>/comprovante/",
        pagar_views.ComprovantePagamentoView.as_view(),
        name="pagar_comprovante",
    ),
    path("pagar/<int:pk>/cancelar/",  pagar_views.ContaPagarCancelarView.as_view(),  name="pagar_cancelar"),

    # ── Categorias Financeiras ────────────────────────────────────────────────
    path("categorias-financeiras/",                       pc_views.PlanoContasListView.as_view(),        name="plano_contas_list"),
    path("categorias-financeiras/nova/",                  pc_views.PlanoContasCreateView.as_view(),      name="plano_contas_criar"),
    path("categorias-financeiras/<int:pk>/editar/",       pc_views.PlanoContasEditView.as_view(),        name="plano_contas_editar"),
    path("categorias-financeiras/<int:pk>/toggle-ativo/", pc_views.PlanoContasToggleAtivoView.as_view(), name="plano_contas_toggle"),
    path("plano-contas/", RedirectView.as_view(pattern_name="financeiro:plano_contas_list")),
    path("plano-contabil/", plano_contabil_views.PlanoContabilListView.as_view(), name="plano_contabil_list"),

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
