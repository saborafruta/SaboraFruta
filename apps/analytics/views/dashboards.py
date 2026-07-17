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
    tipo_fiscal = request.GET.get('tipo_fiscal', '')  # emitidas | nfce | nfe | nao_fiscal | cancelada
    desconsiderar_canceladas = request.GET.get('desconsiderar_canceladas', '1') != '0'

    if not data_ini and not data_fim and not tipo_fiscal:
        hoje = date.today()
        data_ini = hoje.isoformat()
        data_fim = hoje.isoformat()

    qs = (
        VendaPDV.objects
        .for_filial(request.filial_ativa)
        .filter(status__in=['finalizada', 'cancelada'])
        .select_related('cliente', 'documento_fiscal', 'filial')
        .prefetch_related('pagamentos__forma_pagamento')
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

    contadores = {
        '': qs.exclude(status='cancelada').count() if desconsiderar_canceladas else qs.count(),
        'emitidas': qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento__in=['nfe', 'nfce'],
        ).count(),
        'nfe': qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfe',
        ).count(),
        'nfce': qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfce',
        ).count(),
        'cancelada': qs.filter(status='cancelada').count(),
    }

    if tipo_fiscal == 'emitidas':
        qs = qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento__in=['nfe', 'nfce'],
        )
    elif tipo_fiscal == 'nfce':
        qs = qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfce',
        )
    elif tipo_fiscal == 'nfe':
        qs = qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfe',
        )
    elif tipo_fiscal == 'nao_fiscal':
        qs = qs.filter(documento_fiscal__isnull=True, status='finalizada')
    elif tipo_fiscal == 'cancelada':
        qs = qs.filter(status='cancelada')
    elif desconsiderar_canceladas:
        qs = qs.exclude(status='cancelada')

    exibir_totalizador = tipo_fiscal != 'cancelada'
    qs_totalizador = qs.exclude(status='cancelada')
    valor_totalizador = (
        qs_totalizador.aggregate(total=Sum('valor_total'))['total'] or 0
        if exibir_totalizador else None
    )
    quantidade_totalizador = qs_totalizador.count() if exibir_totalizador else 0

    total = qs.count()
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    query_base = request.GET.copy()
    query_base.pop('page', None)
    query_base.pop('tipo_fiscal', None)
    if data_ini:
        query_base['data_ini'] = data_ini
    if data_fim:
        query_base['data_fim'] = data_fim
    query_base['desconsiderar_canceladas'] = '1' if desconsiderar_canceladas else '0'
    atalhos = []
    for valor, rotulo in [
        ('', 'Todas'),
        ('emitidas', 'Emitidas'),
        ('nfe', 'NF-e'),
        ('nfce', 'NFC-e'),
        ('cancelada', 'Vendas canceladas'),
    ]:
        query = query_base.copy()
        if valor:
            query['tipo_fiscal'] = valor
        atalhos.append({
            'valor': valor,
            'rotulo': rotulo,
            'quantidade': contadores[valor],
            'url': '?' + query.urlencode() if query else request.path,
            'ativo': tipo_fiscal == valor,
        })

    return render(request, 'analytics/vendas.html', {
        'title': 'Histórico de Vendas',
        'page_obj': page_obj,
        'total': total,
        'atalhos': atalhos,
        'exibir_totalizador': exibir_totalizador,
        'valor_totalizador': valor_totalizador,
        'quantidade_totalizador': quantidade_totalizador,
        'filtros': {
            'pedido': pedido_q,
            'cliente': cliente_q,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'tipo_fiscal': tipo_fiscal,
            'desconsiderar_canceladas': desconsiderar_canceladas,
        },
    })
