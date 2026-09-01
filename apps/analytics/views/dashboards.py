"""Dashboards: operacional, comercial, produção, DRE."""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.shortcuts import get_object_or_404, render
from django.db.models import (
    Sum, Count, Avg, F, Q, Case, DecimalField, IntegerField, OuterRef,
    Subquery, Value, When,
)
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone

from apps.core.services.permissions import requer_permissao
from apps.financeiro.services.receita import ajuste_por_grupo, ajuste_total
from apps.estoque.models import Estoque, AlertaVencimento
from apps.producao.models import OrdemProducao
from apps.producao.constants.enums import StatusOP
from apps.producao.services.rendimento_service import RendimentoService
from apps.produtos.models import LinhaProducao, Produto
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.models import DREConsolidado
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.pdv.models import PagamentoVendaPDV, VendaPDV


ORDENACAO_HISTORICO_VENDAS = {
    'pedido': 'numero_venda',
    'data': 'data_venda',
    'cliente': 'cliente__razao_social',
    'pagamento': 'pagamento_ordenacao',
    'total': 'valor_total',
    'financeiro': 'status_financeiro_ordem',
    'saldo': 'saldo_restante',
    'fiscal': 'documento_fiscal__status',
    'nnf': 'documento_fiscal__numero',
}


@requer_permissao('relatorios', 'ver')
def dashboard_operacional(request):
    hoje = timezone.localdate()
    linhas = LinhaProducao.objects.filter(ativo=True)
    blocos = []
    for linha in linhas:
        # A OP chega à linha PELO PRODUTO — ela não tem `linha_producao`.
        ops_abertas = OrdemProducao.objects.for_filial(request.filial).filter(
            produto_acabado__linha_producao=linha,
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


def _filtros_historico_vendas(request):
    """
    Lê os filtros da querystring do Histórico de Vendas, aplicando o
    padrão "hoje" quando nada foi informado. Compartilhado pela tela e
    pelo relatório imprimível, para que os dois nunca divirjam.
    """
    f = {
        'pedido': request.GET.get('pedido', '').strip(),
        'cliente': request.GET.get('cliente', '').strip(),
        'data_ini': request.GET.get('data_ini', ''),
        'data_fim': request.GET.get('data_fim', ''),
        # emitidas | nfce | nfe | nao_fiscal | cancelada
        'tipo_fiscal': request.GET.get('tipo_fiscal', ''),
        # '' (todas) | balcao | delivery
        'tipo_venda': request.GET.get('tipo_venda', ''),
        'desconsiderar_canceladas': request.GET.get('desconsiderar_canceladas', '1') != '0',
        'ordem': request.GET.get('ordem', '-data'),
    }
    if f['tipo_venda'] not in ('balcao', 'delivery'):
        f['tipo_venda'] = ''
    if not f['data_ini'] and not f['data_fim'] and not f['tipo_fiscal'] and not f['tipo_venda']:
        hoje = timezone.localdate().isoformat()
        f['data_ini'] = hoje
        f['data_fim'] = hoje
    return f


def _anotar_financeiro_vendas(qs, filial):
    """Resume os títulos originados no PDV usando o vínculo técnico pelo ID da venda."""
    campo_monetario = DecimalField(max_digits=14, decimal_places=2)
    saldos = (
        ContaReceber.objects.for_filial(filial)
        .filter(documento_tipo='venda_pdv', documento_id=OuterRef('pk'))
        .exclude(status=StatusContaReceber.CANCELADO)
        .values('documento_id')
        .annotate(total=Sum('valor_saldo'))
        .values('total')
    )
    primeiro_pagamento = (
        PagamentoVendaPDV.objects
        .filter(venda_pdv_id=OuterRef('pk'))
        .order_by('forma_pagamento__descricao', 'pk')
        .values('forma_pagamento__descricao')
    )
    return qs.annotate(
        saldo_restante=Coalesce(
            Subquery(saldos[:1], output_field=campo_monetario),
            Value(Decimal('0.00'), output_field=campo_monetario),
        ),
        pagamento_ordenacao=Coalesce(Subquery(primeiro_pagamento[:1]), Value('')),
    ).annotate(
        status_financeiro_ordem=Case(
            When(status='cancelada', then=Value(2)),
            When(saldo_restante__gt=0, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )


def _aplicar_ordenacao_vendas(qs, ordem):
    descendente = ordem.startswith('-')
    chave = ordem[1:] if descendente else ordem
    campo = ORDENACAO_HISTORICO_VENDAS.get(chave)
    if not campo:
        ordem, campo, descendente = '-data', 'data_venda', True
    prefixo = '-' if descendente else ''
    return qs.order_by(f'{prefixo}{campo}', '-pk'), ordem


def _urls_ordenacao_vendas(request, ordem_atual):
    atual_desc = ordem_atual.startswith('-')
    atual_chave = ordem_atual[1:] if atual_desc else ordem_atual
    urls = {}
    for chave in ORDENACAO_HISTORICO_VENDAS:
        proxima = f'-{chave}' if atual_chave == chave and not atual_desc else chave
        params = request.GET.copy()
        params.pop('page', None)
        params['ordem'] = proxima
        urls[chave] = params.urlencode()
    return urls


def _queryset_historico_vendas(request, f):
    """Vendas da filial já filtradas por pedido/cliente/período (sem tipo fiscal)."""
    qs = (
        VendaPDV.objects
        .for_filial(request.filial_ativa)
        .filter(status__in=['finalizada', 'cancelada'])
        .select_related('cliente', 'documento_fiscal', 'filial')
        .prefetch_related('pagamentos__forma_pagamento')
    )

    if f['pedido']:
        try:
            qs = qs.filter(numero_venda=int(f['pedido']))
        except ValueError:
            qs = qs.none()

    if f['cliente']:
        qs = qs.filter(
            Q(cliente__razao_social__icontains=f['cliente']) |
            Q(cliente__cpf_cnpj__icontains=f['cliente'])
        )

    if f['data_ini']:
        qs = qs.filter(data_venda__date__gte=f['data_ini'])
    if f['data_fim']:
        qs = qs.filter(data_venda__date__lte=f['data_fim'])

    if f.get('tipo_venda') == 'delivery':
        qs = qs.filter(delivery=True)
    elif f.get('tipo_venda') == 'balcao':
        qs = qs.filter(delivery=False)

    qs = _anotar_financeiro_vendas(qs, request.filial_ativa)
    qs, f['ordem'] = _aplicar_ordenacao_vendas(qs, f.get('ordem', '-data'))
    return qs


def _aplicar_tipo_fiscal(qs, tipo_fiscal, desconsiderar_canceladas):
    """Recorte por situação fiscal usado nas abas da tela e no relatório."""
    if tipo_fiscal == 'emitidas':
        return qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento__in=['nfe', 'nfce'],
        )
    if tipo_fiscal == 'nfce':
        return qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfce',
        )
    if tipo_fiscal == 'nfe':
        return qs.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfe',
        )
    if tipo_fiscal == 'nao_fiscal':
        return qs.filter(documento_fiscal__isnull=True, status='finalizada')
    if tipo_fiscal == 'cancelada':
        return qs.filter(status='cancelada')
    if desconsiderar_canceladas:
        return qs.exclude(status='cancelada')
    return qs


@requer_permissao('relatorios', 'ver')
def historico_vendas(request):
    f = _filtros_historico_vendas(request)
    pedido_q = f['pedido']
    cliente_q = f['cliente']
    data_ini = f['data_ini']
    data_fim = f['data_fim']
    tipo_fiscal = f['tipo_fiscal']
    desconsiderar_canceladas = f['desconsiderar_canceladas']

    qs = _queryset_historico_vendas(request, f)

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

    qs = _aplicar_tipo_fiscal(qs, tipo_fiscal, desconsiderar_canceladas)

    exibir_totalizador = tipo_fiscal != 'cancelada'
    qs_totalizador = qs.exclude(status='cancelada')
    # Doacao/Permuta nao sao receita (ver financeiro.services.receita).
    valor_totalizador = (
        max(0, (qs_totalizador.aggregate(total=Sum('valor_total'))['total'] or 0)
               - ajuste_total(qs_totalizador))
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

    situacao_xml = 'todas'
    if tipo_fiscal in {'emitidas', 'nfe', 'nfce'}:
        situacao_xml = 'emitidas'
    elif tipo_fiscal == 'cancelada':
        situacao_xml = 'canceladas'
    parametros_xml = {
        'origem': 'vendas',
        'situacao': situacao_xml,
    }
    if tipo_fiscal in {'nfe', 'nfce'}:
        parametros_xml['tipo_documento'] = tipo_fiscal
    if data_ini:
        parametros_xml['data_ini'] = data_ini
    if data_fim:
        parametros_xml['data_fim'] = data_fim
    exportar_xml_vendas_url = (
        f"{reverse('fiscal:documento-saida-exportar-xml')}?"
        f"{urlencode(parametros_xml)}"
    )

    return render(request, 'analytics/vendas.html', {
        'title': 'Histórico de Vendas',
        'page_obj': page_obj,
        'total': total,
        'atalhos': atalhos,
        'exibir_totalizador': exibir_totalizador,
        'valor_totalizador': valor_totalizador,
        'quantidade_totalizador': quantidade_totalizador,
        'exportar_xml_vendas_url': exportar_xml_vendas_url,
        'ordem': f['ordem'],
        'sort_urls': _urls_ordenacao_vendas(request, f['ordem']),
        'pode_editar_financeiro': request.user.tem_permissao('financeiro', 'editar'),
        'filtros': {
            'pedido': pedido_q,
            'cliente': cliente_q,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'tipo_fiscal': tipo_fiscal,
            'tipo_venda': f['tipo_venda'],
            'desconsiderar_canceladas': desconsiderar_canceladas,
        },
    })


@requer_permissao('relatorios', 'ver')
def historico_venda_financeiro(request, pk):
    venda = get_object_or_404(
        VendaPDV.objects.for_filial(request.filial_ativa).select_related('cliente'),
        pk=pk,
    )
    contas = list(
        ContaReceber.objects.for_filial(request.filial_ativa)
        .filter(documento_tipo='venda_pdv', documento_id=venda.pk)
        .exclude(status=StatusContaReceber.CANCELADO)
        .select_related('forma_pagamento', 'conta_bancaria')
        .prefetch_related('pagamentos__forma_pagamento', 'pagamentos__conta_bancaria')
        .order_by('data_vencimento', 'pk')
    )
    saldo_restante = sum((conta.valor_saldo for conta in contas), Decimal('0.00'))
    valor_titulos = sum((conta.valor_original for conta in contas), Decimal('0.00'))
    recebido_titulos = sum((conta.valor_pago for conta in contas), Decimal('0.00'))
    recebido_imediato = max((venda.valor_total or Decimal('0.00')) - valor_titulos, Decimal('0.00'))
    recebido_total = recebido_imediato + recebido_titulos
    return render(request, 'analytics/_venda_financeiro.html', {
        'venda': venda,
        'contas': contas,
        'saldo_restante': saldo_restante,
        'recebido_total': recebido_total,
        'recebido_imediato': recebido_imediato,
        'recebido_titulos': recebido_titulos,
        'pode_editar_financeiro': request.user.tem_permissao('financeiro', 'editar'),
    })


@requer_permissao('relatorios', 'ver')
def historico_vendas_relatorio(request):
    """
    Versão imprimível do Histórico de Vendas: respeita exatamente os
    mesmos filtros da tela (por isso reaproveita os helpers acima), mas
    sem paginação — o relatório traz todos os registros do período.
    """
    f = _filtros_historico_vendas(request)
    qs = _aplicar_tipo_fiscal(
        _queryset_historico_vendas(request, f),
        f['tipo_fiscal'],
        f['desconsiderar_canceladas'],
    )

    # Teto de segurança: um período muito largo geraria um PDF gigante e
    # um request lento. Avisa no relatório quando houver corte.
    LIMITE = 2000
    total = qs.count()
    vendas = list(qs[:LIMITE])

    # Totais desconsiderando canceladas, quebrados por tipo de venda. O
    # balcão/delivery sai num único aggregate com Sum condicional para não
    # disparar uma query extra por linha do resumo.
    qs_totais = qs.exclude(status='cancelada')
    totais = qs_totais.aggregate(
        total=Sum('valor_total'),
        total_balcao=Sum('valor_total', filter=Q(delivery=False)),
        total_delivery=Sum('valor_total', filter=Q(delivery=True)),
        qtd_balcao=Count('id', filter=Q(delivery=False)),
        qtd_delivery=Count('id', filter=Q(delivery=True)),
    )
    # Desconta Doacao/Permuta de cada subtotal. O ajuste vem agrupado pelo
    # mesmo flag `delivery` usado no aggregate, senao balcao + delivery
    # deixaria de fechar com o total.
    ajustes = ajuste_por_grupo(qs_totais, 'venda_pdv__delivery')
    aj_balcao = ajustes.get((False,), 0)
    aj_delivery = ajustes.get((True,), 0)

    valor_balcao = max(0, (totais['total_balcao'] or 0) - aj_balcao)
    valor_delivery = max(0, (totais['total_delivery'] or 0) - aj_delivery)
    valor_total = valor_balcao + valor_delivery

    rotulos_tipo = {
        '': 'Todas as vendas',
        'emitidas': 'Somente notas emitidas',
        'nfe': 'Somente NF-e',
        'nfce': 'Somente NFC-e',
        'nao_fiscal': 'Somente sem documento fiscal',
        'cancelada': 'Somente vendas canceladas',
    }

    rotulos_tipo_venda = {
        '': 'Balcão e delivery',
        'balcao': 'Somente balcão',
        'delivery': 'Somente delivery',
    }

    return render(request, 'analytics/vendas_relatorio.html', {
        'title': 'Relatório de Vendas',
        'vendas': vendas,
        'total': total,
        'truncado': total > LIMITE,
        'limite': LIMITE,
        'valor_total': valor_total,
        'valor_balcao': valor_balcao,
        'valor_delivery': valor_delivery,
        'qtd_balcao': totais['qtd_balcao'] or 0,
        'qtd_delivery': totais['qtd_delivery'] or 0,
        'quantidade_considerada': (totais['qtd_balcao'] or 0) + (totais['qtd_delivery'] or 0),
        'filtros': f,
        'rotulo_tipo': rotulos_tipo.get(f['tipo_fiscal'], 'Todas as vendas'),
        'rotulo_tipo_venda': rotulos_tipo_venda.get(f['tipo_venda'], 'Balcão e delivery'),
        'gerado_em': timezone.localtime(),
    })
