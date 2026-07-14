"""Dashboards: operacional, comercial, produção, DRE."""
from datetime import date, timedelta
from django.shortcuts import render
from django.db.models import Sum, Count, Avg, F, Q
from django.core.paginator import Paginator

from apps.core.services.permissions import requer_permissao
from apps.estoque.models import Estoque, AlertaVencimento
from apps.producao.models import OrdemProducao
from apps.producao.constants.enums import StatusOP
from apps.producao.services.rendimento_service import RendimentoService
from apps.produtos.models import LinhaProducao, Produto
from apps.financeiro.models import DREConsolidado
from apps.pdv.models import VendaPDV


@requer_permissao('relatorios', 'ver')
def dashboard_operacional(request):
    hoje = date.today()
    linhas = LinhaProducao.objects.filter(ativo=True)
    blocos = []
    for linha in linhas:
        ops_abertas = OrdemProducao.objects.for_filial(request.filial).filter(
            linha_producao=linha,
            status__in=[StatusOP.ABERTA, StatusOP.EM_PRODUCAO],
        ).count()
        alertas = AlertaVencimento.objects.for_filial(request.filial).filter(
            linha_producao=linha, resolvido=False,
        ).count()
        blocos.append({
            "linha": linha,
            "ops_abertas": ops_abertas,
            "alertas": alertas,
            "rendimento": RendimentoService.rendimento_medio(linha, request.filial),
        })
    return render(request, "analytics/operacional.html", {
        "title": "Dashboard Operacional", "blocos": blocos,
    })


@requer_permissao('relatorios', 'ver')
def dashboard_comercial(request):
    return render(request, "analytics/comercial.html", {"title": "Dashboard Comercial"})


@requer_permissao('relatorios', 'ver')
def dashboard_producao(request):
    return render(request, "analytics/producao.html", {"title": "Dashboard de Produção"})


@requer_permissao('relatorios', 'ver')
def dashboard_dre(request):
    qs = DREConsolidado.objects.for_filial(request.filial).order_by(
        "-competencia", "linha_producao",
    )[:36]
    return render(request, "analytics/dre.html", {
        "title": "DRE — Visão Dinâmica", "dres": qs,
    })


@requer_permissao('relatorios', 'ver')
def historico_vendas(request):
    pedido_q   = request.GET.get('pedido', '').strip()
    cliente_q  = request.GET.get('cliente', '').strip()
    data_ini   = request.GET.get('data_ini', '')
    data_fim   = request.GET.get('data_fim', '')
    tipo_fiscal = request.GET.get('tipo_fiscal', '')  # nfce | nfe | nao_fiscal | cancelada

    qs = (
        VendaPDV.objects
        .for_filial(request.filial_ativa)
        .filter(status__in=['finalizada', 'cancelada'])
        .select_related('cliente', 'documento_fiscal')
        .order_by('-data_venda')
    )

    if pedido_q:
        try:
            qs = qs.filter(numero_venda=int(pedido_q))
        except ValueError:
            qs = qs.none()

    if cliente_q:
        qs = qs.filter(
            Q(cliente__razao_social__icontains=cliente_q) |
            Q(cliente__cpf_cnpj__icontains=cliente_q)
        )

    if data_ini:
        qs = qs.filter(data_venda__date__gte=data_ini)
    if data_fim:
        qs = qs.filter(data_venda__date__lte=data_fim)

    if tipo_fiscal == 'nfce':
        qs = qs.filter(documento_fiscal__tipo_documento='nfce')
    elif tipo_fiscal == 'nfe':
        qs = qs.filter(documento_fiscal__tipo_documento='nfe')
    elif tipo_fiscal == 'nao_fiscal':
        qs = qs.filter(documento_fiscal__isnull=True, status='finalizada')
    elif tipo_fiscal == 'cancelada':
        qs = qs.filter(status='cancelada')

    total = qs.count()
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'analytics/vendas.html', {
        'title': 'Histórico de Vendas',
        'page_obj': page_obj,
        'total': total,
        'filtros': {
            'pedido': pedido_q,
            'cliente': cliente_q,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'tipo_fiscal': tipo_fiscal,
        },
    })
