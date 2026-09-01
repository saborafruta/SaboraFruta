"""Dashboards: operacional, comercial, produção, DRE."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from django.shortcuts import get_object_or_404, render
from django.db.models import (
    Sum, Count, Avg, F, Q, Case, DecimalField, IntegerField, OuterRef,
    Subquery, Value, When, Exists,
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
from apps.moda.models import PedidoProducao
from apps.moda.services.financeiro import FinanceiroPedidoService


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
        # '' (todas) | balcao | delivery | op
        'tipo_venda': request.GET.get('tipo_venda', ''),
        'desconsiderar_canceladas': request.GET.get('desconsiderar_canceladas', '1') != '0',
        'ordem': request.GET.get('ordem', '-data'),
    }
    if f['tipo_venda'] not in ('balcao', 'delivery', 'op'):
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

    if f.get('tipo_venda') == 'op':
        return _anotar_financeiro_vendas(qs.none(), request.filial_ativa)

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


def _queryset_ops_historico(request, f):
    """Pedidos de produção com financeiro válido, exibidos como origem OP."""
    campo_monetario = DecimalField(max_digits=14, decimal_places=2)
    titulos = (
        ContaReceber.objects.for_filial(request.filial_ativa)
        .filter(
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id=OuterRef('pk'),
        )
        .exclude(status=StatusContaReceber.CANCELADO)
    )
    resumo = (
        titulos.order_by().values('documento_id')
        .annotate(
            total=Sum('valor_final'),
            recebido=Sum('valor_pago'),
            saldo=Sum('valor_saldo'),
        )
    )
    primeira_forma = (
        titulos.order_by('forma_pagamento__descricao', 'pk')
        .values('forma_pagamento__descricao')
    )
    qs = (
        PedidoProducao.objects.for_filial(request.filial_ativa)
        .filter(Exists(titulos))
        .select_related('cliente', 'forma_pagamento')
        .prefetch_related('ordens')
        .annotate(
            valor_total_historico=Coalesce(
                Subquery(resumo.values('total')[:1], output_field=campo_monetario),
                Value(Decimal('0.00'), output_field=campo_monetario),
            ),
            valor_recebido_historico=Coalesce(
                Subquery(resumo.values('recebido')[:1], output_field=campo_monetario),
                Value(Decimal('0.00'), output_field=campo_monetario),
            ),
            saldo_restante=Coalesce(
                Subquery(resumo.values('saldo')[:1], output_field=campo_monetario),
                Value(Decimal('0.00'), output_field=campo_monetario),
            ),
            pagamento_ordenacao=Coalesce(Subquery(primeira_forma[:1]), Value('')),
        )
    )

    if f.get('tipo_venda') in {'balcao', 'delivery'}:
        return qs.none()
    if f['pedido']:
        try:
            qs = qs.filter(numero=int(f['pedido']))
        except ValueError:
            qs = qs.none()
    if f['cliente']:
        qs = qs.filter(
            Q(cliente__razao_social__icontains=f['cliente']) |
            Q(cliente__cpf_cnpj__icontains=f['cliente'])
        )
    if f['data_ini']:
        qs = qs.filter(data_pedido__gte=f['data_ini'])
    if f['data_fim']:
        qs = qs.filter(data_pedido__lte=f['data_fim'])
    return qs


def _aplicar_tipo_fiscal_op(qs, tipo_fiscal, desconsiderar_canceladas):
    """OP não tem documento fiscal nesta origem; ainda participa do recorte não fiscal."""
    cancelado = PedidoProducao.Status.CANCELADO
    if tipo_fiscal in {'emitidas', 'nfe', 'nfce'}:
        return qs.none()
    if tipo_fiscal == 'cancelada':
        return qs.filter(status=cancelado)
    if tipo_fiscal == 'nao_fiscal':
        return qs.exclude(status=cancelado)
    if desconsiderar_canceladas:
        return qs.exclude(status=cancelado)
    return qs


def _formas_ops(pedidos, filial):
    ids = [pedido.pk for pedido in pedidos]
    formas = {pedido_id: [] for pedido_id in ids}
    vistos = {pedido_id: set() for pedido_id in ids}
    contas = (
        ContaReceber.objects.for_filial(filial)
        .filter(
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id__in=ids,
        )
        .exclude(status=StatusContaReceber.CANCELADO)
        .select_related('forma_pagamento')
        .order_by('documento_id', 'parcela', 'pk')
    )
    for conta in contas:
        forma = conta.forma_pagamento
        chave = forma.pk if forma else None
        if chave in vistos[conta.documento_id]:
            continue
        vistos[conta.documento_id].add(chave)
        formas[conta.documento_id].append(SimpleNamespace(forma_pagamento=forma))
    return formas


def _registro_pdv(venda):
    documento = getattr(venda, 'documento_fiscal', None)
    return SimpleNamespace(
        origem_historico='pdv', pk=venda.pk, numero_venda=venda.numero_venda,
        data_venda=venda.data_venda, cliente=venda.cliente,
        pagamentos_historico=list(venda.pagamentos.all()),
        pagamento_ordenacao=venda.pagamento_ordenacao or '',
        valor_total=venda.valor_total, saldo_restante=venda.saldo_restante,
        status_financeiro_ordem=venda.status_financeiro_ordem,
        status=venda.status, delivery=venda.delivery, documento_fiscal=documento,
        financeiro_url=reverse('analytics:venda-financeiro', args=[venda.pk]),
        detalhe_url='', op_numeros=[],
    )


def _registros_ops(qs, filial):
    pedidos = list(qs)
    formas = _formas_ops(pedidos, filial)
    registros = []
    for pedido in pedidos:
        cancelada = pedido.status == PedidoProducao.Status.CANCELADO
        data_venda = timezone.make_aware(datetime.combine(pedido.data_pedido, time.min))
        registros.append(SimpleNamespace(
            origem_historico='op', pk=pedido.pk, numero_venda=pedido.numero,
            data_venda=data_venda, cliente=pedido.cliente,
            pagamentos_historico=formas.get(pedido.pk, []),
            pagamento_ordenacao=pedido.pagamento_ordenacao or '',
            valor_total=pedido.valor_total_historico,
            valor_recebido=pedido.valor_recebido_historico,
            saldo_restante=pedido.saldo_restante,
            status_financeiro_ordem=(2 if cancelada else 1 if pedido.saldo_restante > 0 else 0),
            status='cancelada' if cancelada else 'finalizada', delivery=False,
            documento_fiscal=None,
            financeiro_url=reverse('analytics:op-financeiro', args=[pedido.pk]),
            detalhe_url=reverse('moda:op2-detail', args=[pedido.pk]),
            op_numeros=[ordem.numero for ordem in pedido.ordens.all()],
        ))
    return registros


def _ordenar_registros_historico(registros, ordem):
    descendente = ordem.startswith('-')
    chave = ordem[1:] if descendente else ordem
    if chave not in ORDENACAO_HISTORICO_VENDAS:
        chave, ordem, descendente = 'data', '-data', True

    def valor(registro):
        if chave == 'pedido':
            return registro.numero_venda
        if chave == 'data':
            return registro.data_venda
        if chave == 'cliente':
            return (getattr(registro.cliente, 'razao_social', '') or '').casefold()
        if chave == 'pagamento':
            return (registro.pagamento_ordenacao or '').casefold()
        if chave == 'total':
            return registro.valor_total or Decimal('0')
        if chave == 'financeiro':
            return registro.status_financeiro_ordem
        if chave == 'saldo':
            return registro.saldo_restante or Decimal('0')
        if chave == 'fiscal':
            return (getattr(registro.documento_fiscal, 'status', '') or '').casefold()
        if chave == 'nnf':
            return getattr(registro.documento_fiscal, 'numero', 0) or 0
        return registro.data_venda

    registros.sort(
        key=lambda registro: (valor(registro), registro.data_venda, registro.pk),
        reverse=descendente,
    )
    return registros, ordem


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

    qs_pdv = _queryset_historico_vendas(request, f)
    qs_op = _queryset_ops_historico(request, f)
    cancelado_op = PedidoProducao.Status.CANCELADO

    contadores = {
        '': (
            (qs_pdv.exclude(status='cancelada').count() + qs_op.exclude(status=cancelado_op).count())
            if desconsiderar_canceladas else qs_pdv.count() + qs_op.count()
        ),
        'emitidas': qs_pdv.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento__in=['nfe', 'nfce'],
        ).count(),
        'nfe': qs_pdv.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfe',
        ).count(),
        'nfce': qs_pdv.filter(
            status='finalizada', documento_fiscal__status='autorizada',
            documento_fiscal__tipo_documento='nfce',
        ).count(),
        'cancelada': (
            qs_pdv.filter(status='cancelada').count()
            + qs_op.filter(status=cancelado_op).count()
        ),
    }

    qs_pdv = _aplicar_tipo_fiscal(qs_pdv, tipo_fiscal, desconsiderar_canceladas)
    qs_op = _aplicar_tipo_fiscal_op(qs_op, tipo_fiscal, desconsiderar_canceladas)

    exibir_totalizador = tipo_fiscal != 'cancelada'
    qs_totalizador_pdv = qs_pdv.exclude(status='cancelada')
    qs_totalizador_op = qs_op.exclude(status=cancelado_op)
    # Doacao/Permuta nao sao receita (ver financeiro.services.receita).
    if exibir_totalizador:
        valor_totalizador_pdv = max(
            0,
            (qs_totalizador_pdv.aggregate(total=Sum('valor_total'))['total'] or 0)
            - ajuste_total(qs_totalizador_pdv),
        )
        valor_totalizador_op = (
            qs_totalizador_op.aggregate(total=Sum('valor_total_historico'))['total']
            or Decimal('0')
        )
        valor_totalizador = valor_totalizador_pdv + valor_totalizador_op
        quantidade_totalizador = qs_totalizador_pdv.count() + qs_totalizador_op.count()
    else:
        valor_totalizador = None
        quantidade_totalizador = 0

    registros = [_registro_pdv(venda) for venda in qs_pdv]
    registros.extend(_registros_ops(qs_op, request.filial_ativa))
    registros, f['ordem'] = _ordenar_registros_historico(registros, f['ordem'])
    total = len(registros)
    paginator = Paginator(registros, 25)
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
        'registro': SimpleNamespace(
            rotulo='Venda', numero=f'{venda.numero_venda:06d}',
            cliente_nome=(venda.cliente.razao_social if venda.cliente else 'Consumidor Final'),
            data=venda.data_venda, valor_total=venda.valor_total,
        ),
        'contas': contas,
        'saldo_restante': saldo_restante,
        'recebido_total': recebido_total,
        'recebido_imediato': recebido_imediato,
        'recebido_titulos': recebido_titulos,
        'pode_editar_financeiro': request.user.tem_permissao('financeiro', 'editar'),
    })


@requer_permissao('relatorios', 'ver')
def historico_op_financeiro(request, pk):
    pedido = get_object_or_404(
        PedidoProducao.objects.for_filial(request.filial_ativa).select_related('cliente'),
        pk=pk,
    )
    contas = list(
        ContaReceber.objects.for_filial(request.filial_ativa)
        .filter(
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id=pedido.pk,
        )
        .exclude(status=StatusContaReceber.CANCELADO)
        .select_related('forma_pagamento', 'conta_bancaria')
        .prefetch_related('pagamentos__forma_pagamento', 'pagamentos__conta_bancaria')
        .order_by('data_vencimento', 'pk')
    )
    valor_total = sum((conta.valor_final for conta in contas), Decimal('0.00'))
    saldo_restante = sum((conta.valor_saldo for conta in contas), Decimal('0.00'))
    recebido_titulos = sum((conta.valor_pago for conta in contas), Decimal('0.00'))
    return render(request, 'analytics/_venda_financeiro.html', {
        'registro': SimpleNamespace(
            rotulo='OP', numero=f'{pedido.numero:06d}',
            cliente_nome=pedido.cliente.razao_social,
            data=timezone.make_aware(datetime.combine(pedido.data_pedido, time.min)),
            valor_total=valor_total,
        ),
        'contas': contas,
        'saldo_restante': saldo_restante,
        'recebido_total': recebido_titulos,
        'recebido_imediato': Decimal('0.00'),
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
    qs_pdv = _aplicar_tipo_fiscal(
        _queryset_historico_vendas(request, f),
        f['tipo_fiscal'],
        f['desconsiderar_canceladas'],
    )
    qs_op = _aplicar_tipo_fiscal_op(
        _queryset_ops_historico(request, f),
        f['tipo_fiscal'],
        f['desconsiderar_canceladas'],
    )

    # Teto de segurança: um período muito largo geraria um PDF gigante e
    # um request lento. Avisa no relatório quando houver corte.
    LIMITE = 2000
    registros = [_registro_pdv(venda) for venda in qs_pdv]
    registros.extend(_registros_ops(qs_op, request.filial_ativa))
    registros, f['ordem'] = _ordenar_registros_historico(registros, f['ordem'])
    total = len(registros)
    vendas = registros[:LIMITE]

    # Totais desconsiderando canceladas, quebrados por tipo de venda. O
    # balcão/delivery sai num único aggregate com Sum condicional para não
    # disparar uma query extra por linha do resumo.
    qs_totais = qs_pdv.exclude(status='cancelada')
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
    qs_ops_totais = qs_op.exclude(status=PedidoProducao.Status.CANCELADO)
    valor_op = (
        qs_ops_totais.aggregate(total=Sum('valor_total_historico'))['total']
        or Decimal('0')
    )
    qtd_op = qs_ops_totais.count()
    valor_total = valor_balcao + valor_delivery + valor_op

    rotulos_tipo = {
        '': 'Todas as vendas',
        'emitidas': 'Somente notas emitidas',
        'nfe': 'Somente NF-e',
        'nfce': 'Somente NFC-e',
        'nao_fiscal': 'Somente sem documento fiscal',
        'cancelada': 'Somente vendas canceladas',
    }

    rotulos_tipo_venda = {
        '': 'Balcão, delivery e OP',
        'balcao': 'Somente balcão',
        'delivery': 'Somente delivery',
        'op': 'Somente OP',
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
        'valor_op': valor_op,
        'qtd_balcao': totais['qtd_balcao'] or 0,
        'qtd_delivery': totais['qtd_delivery'] or 0,
        'qtd_op': qtd_op,
        'quantidade_considerada': (
            (totais['qtd_balcao'] or 0) + (totais['qtd_delivery'] or 0) + qtd_op
        ),
        'filtros': f,
        'rotulo_tipo': rotulos_tipo.get(f['tipo_fiscal'], 'Todas as vendas'),
        'rotulo_tipo_venda': rotulos_tipo_venda.get(f['tipo_venda'], 'Balcão, delivery e OP'),
        'gerado_em': timezone.localtime(),
    })
