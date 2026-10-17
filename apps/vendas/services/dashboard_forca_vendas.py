"""Indicadores comerciais da força de vendas externa."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
)
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.cadastros.models import Cliente
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


ZERO = Decimal('0')

STATUS_APROVADOS = (
    PedidoVenda.Status.CONFIRMADO,
    PedidoVenda.Status.EM_SEPARACAO,
    PedidoVenda.Status.FATURADO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
    PedidoVenda.Status.EM_ENTREGA,
    PedidoVenda.Status.ENTREGUE,
)

ETAPAS = (
    ('analise', 'Em análise', (
        PedidoVenda.Status.RASCUNHO,
        PedidoVenda.Status.AGUARDANDO_APROVACAO,
        PedidoVenda.Status.APROVADO,
    )),
    ('separacao', 'Aguardando separação', (PedidoVenda.Status.CONFIRMADO,)),
    ('separado', 'Separado', (PedidoVenda.Status.EM_SEPARACAO,)),
    ('faturado', 'Faturado', (
        PedidoVenda.Status.FATURADO,
        PedidoVenda.Status.PARCIALMENTE_FATURADO,
    )),
    ('entrega', 'Entrega / concluído', (
        PedidoVenda.Status.EM_ENTREGA,
        PedidoVenda.Status.ENTREGUE,
    )),
)


def _periodo(params):
    hoje = timezone.localdate()
    inicio_padrao = hoje.replace(day=1)
    inicio = parse_date(params.get('inicio', '')) or inicio_padrao
    fim = parse_date(params.get('fim', '')) or hoje
    if fim < inicio:
        inicio, fim = fim, inicio
    return inicio, fim


def _percentual_variacao(atual, anterior):
    atual = Decimal(atual or 0)
    anterior = Decimal(anterior or 0)
    if not anterior:
        return Decimal('0') if not atual else Decimal('100')
    return ((atual - anterior) / anterior * 100).quantize(Decimal('0.1'))


def _regiao_label(cidade, uf):
    cidade = (cidade or '').strip()
    uf = (uf or '').strip()
    if cidade and uf:
        return f'{cidade} / {uf}'
    return cidade or uf or 'Sem região'


def _aplicar_filtros(qs, vendedor_id='', regiao='', pagamento_id=''):
    if vendedor_id and str(vendedor_id).isdigit():
        qs = qs.filter(usuario_id=vendedor_id)
    if regiao:
        cidade, _, uf = regiao.partition('|')
        if cidade:
            qs = qs.filter(cliente__cidade__iexact=cidade)
        else:
            qs = qs.filter(Q(cliente__cidade='') | Q(cliente__cidade__isnull=True))
        if uf:
            qs = qs.filter(cliente__uf__iexact=uf)
        elif cidade:
            qs = qs.filter(Q(cliente__uf='') | Q(cliente__uf__isnull=True))
    if pagamento_id and str(pagamento_id).isdigit():
        qs = qs.filter(forma_pagamento_id=pagamento_id)
    return qs


def _intervalo_datetime(inicio, fim):
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(inicio, time.min), tz),
        timezone.make_aware(datetime.combine(fim, time.max), tz),
    )


def _serie_temporal(qs, inicio, fim):
    dias = (fim - inicio).days + 1
    if dias <= 45:
        truncador = TruncDate('data_emissao')
        formato = '%d/%m'
    elif dias <= 180:
        truncador = TruncWeek('data_emissao')
        formato = '%d/%m'
    else:
        truncador = TruncMonth('data_emissao')
        formato = '%b/%y'

    linhas = list(
        qs.filter(status__in=STATUS_APROVADOS)
        .annotate(periodo=truncador)
        .values('periodo')
        .annotate(valor=Coalesce(Sum('valor_total'), ZERO))
        .order_by('periodo')
    )
    maior = max((linha['valor'] or ZERO for linha in linhas), default=ZERO)
    for linha in linhas:
        valor = linha['valor'] or ZERO
        linha['altura'] = max(6, int((valor / maior) * 100)) if maior else 6
        linha['label'] = linha['periodo'].strftime(formato)
    return linhas


def montar_dashboard(*, pedidos_visiveis, filial, params):
    """Monta o contexto do painel respeitando o queryset já autorizado."""
    inicio, fim = _periodo(params)
    vendedor_id = (params.get('vendedor') or '').strip()
    regiao = (params.get('regiao') or '').strip()
    pagamento_id = (params.get('pagamento') or '').strip()

    opcoes_vendedores = list(
        pedidos_visiveis.exclude(usuario_id=None)
        .values('usuario_id', 'usuario__nome')
        .distinct().order_by('usuario__nome')
    )
    regioes_brutas = pedidos_visiveis.values_list(
        'cliente__cidade', 'cliente__uf',
    ).distinct().order_by('cliente__uf', 'cliente__cidade')
    opcoes_regioes = [
        {
            'valor': f'{cidade or ""}|{uf or ""}',
            'label': _regiao_label(cidade, uf),
        }
        for cidade, uf in regioes_brutas
    ]
    opcoes_pagamentos = list(
        pedidos_visiveis.exclude(forma_pagamento_id=None)
        .values('forma_pagamento_id', 'forma_pagamento__descricao')
        .distinct().order_by('forma_pagamento__descricao')
    )

    base_filtrada = _aplicar_filtros(
        pedidos_visiveis,
        vendedor_id=vendedor_id,
        regiao=regiao,
        pagamento_id=pagamento_id,
    )
    dt_inicio, dt_fim = _intervalo_datetime(inicio, fim)
    periodo = base_filtrada.filter(data_emissao__range=(dt_inicio, dt_fim))
    validos = periodo.exclude(status=PedidoVenda.Status.CANCELADO)
    aprovados = validos.filter(status__in=STATUS_APROVADOS)

    resumo = validos.aggregate(
        pedidos=Count('pk'),
        valor_enviado=Coalesce(Sum('valor_total'), ZERO),
    )
    vendas = aprovados.aggregate(
        pedidos=Count('pk'),
        valor=Coalesce(Sum('valor_total'), ZERO),
        descontos=Coalesce(Sum('valor_desconto'), ZERO),
        clientes=Count('cliente_id', distinct=True),
    )
    vendas['ticket_medio'] = (
        vendas['valor'] / vendas['pedidos'] if vendas['pedidos'] else ZERO
    )
    vendas['conversao'] = (
        Decimal(vendas['pedidos']) / Decimal(resumo['pedidos']) * 100
        if resumo['pedidos'] else ZERO
    ).quantize(Decimal('0.1'))

    duracao = (fim - inicio).days + 1
    fim_anterior = inicio - timedelta(days=1)
    inicio_anterior = fim_anterior - timedelta(days=duracao - 1)
    dt_inicio_anterior, dt_fim_anterior = _intervalo_datetime(inicio_anterior, fim_anterior)
    anterior = base_filtrada.filter(
        data_emissao__range=(dt_inicio_anterior, dt_fim_anterior),
        status__in=STATUS_APROVADOS,
    ).aggregate(
        pedidos=Count('pk'),
        valor=Coalesce(Sum('valor_total'), ZERO),
    )
    vendas['variacao_valor'] = _percentual_variacao(vendas['valor'], anterior['valor'])
    vendas['variacao_pedidos'] = _percentual_variacao(vendas['pedidos'], anterior['pedidos'])

    itens_aprovados = ItemPedidoVenda.objects.filter(pedido__in=aprovados)
    custo = ExpressionWrapper(
        F('custo_unitario_snapshot') * F('quantidade'),
        output_field=DecimalField(max_digits=18, decimal_places=4),
    )
    margem = itens_aprovados.aggregate(
        valor=Coalesce(Sum(F('valor_total') - custo), ZERO),
    )['valor']
    vendas['margem_valor'] = margem
    vendas['margem_percentual'] = (
        (margem / vendas['valor']) * 100 if vendas['valor'] else ZERO
    ).quantize(Decimal('0.1'))

    pipeline = []
    maior_etapa = 0
    for chave, label, statuses in ETAPAS:
        dados = periodo.filter(status__in=statuses).aggregate(
            quantidade=Count('pk'),
            valor=Coalesce(Sum('valor_total'), ZERO),
        )
        maior_etapa = max(maior_etapa, dados['quantidade'])
        pipeline.append({'chave': chave, 'label': label, **dados})
    for etapa in pipeline:
        etapa['largura'] = max(
            5,
            round(etapa['quantidade'] / maior_etapa * 100),
        ) if maior_etapa else 5

    vendedores = list(
        aprovados.values('usuario_id', 'usuario__nome')
        .annotate(
            valor=Coalesce(Sum('valor_total'), ZERO),
            pedidos=Count('pk'),
            clientes=Count('cliente_id', distinct=True),
        )
        .order_by('-valor', 'usuario__nome')[:8]
    )
    maior_vendedor = vendedores[0]['valor'] if vendedores else ZERO
    for posicao, vendedor in enumerate(vendedores, 1):
        vendedor['posicao'] = posicao
        vendedor['ticket'] = vendedor['valor'] / vendedor['pedidos']
        vendedor['largura'] = int(vendedor['valor'] / maior_vendedor * 100) if maior_vendedor else 0

    regioes = []
    for linha in (
        aprovados.values('cliente__cidade', 'cliente__uf')
        .annotate(
            valor=Coalesce(Sum('valor_total'), ZERO),
            pedidos=Count('pk'),
            clientes=Count('cliente_id', distinct=True),
        )
        .order_by('-valor')[:8]
    ):
        linha['label'] = _regiao_label(linha['cliente__cidade'], linha['cliente__uf'])
        regioes.append(linha)
    maior_regiao = regioes[0]['valor'] if regioes else ZERO
    for posicao, linha in enumerate(regioes, 1):
        linha['posicao'] = posicao
        linha['largura'] = int(linha['valor'] / maior_regiao * 100) if maior_regiao else 0

    produtos = list(
        itens_aprovados.values(
            'produto_id', 'produto__descricao', 'produto__codigo',
            'produto__categoria__nome', 'produto__unidade_medida__sigla',
        )
        .annotate(
            quantidade=Coalesce(Sum('quantidade'), ZERO),
            valor=Coalesce(Sum('valor_total'), ZERO),
            pedidos=Count('pedido_id', distinct=True),
        )
        .order_by('-valor', 'produto__descricao')[:10]
    )

    pagamentos = list(
        aprovados.values('forma_pagamento__descricao')
        .annotate(valor=Coalesce(Sum('valor_total'), ZERO), pedidos=Count('pk'))
        .order_by('-valor')
    )
    for pagamento in pagamentos:
        pagamento['label'] = pagamento['forma_pagamento__descricao'] or 'Não informado'
        pagamento['percentual'] = (
            pagamento['valor'] / vendas['valor'] * 100 if vendas['valor'] else ZERO
        ).quantize(Decimal('0.1'))

    agora = timezone.now()
    analise_atrasada = base_filtrada.filter(
        status__in=(PedidoVenda.Status.RASCUNHO, PedidoVenda.Status.AGUARDANDO_APROVACAO),
        data_emissao__lt=agora - timedelta(hours=48),
    ).count()
    separacao_atrasada = base_filtrada.filter(
        status=PedidoVenda.Status.CONFIRMADO,
        data_emissao__lt=agora - timedelta(hours=24),
    ).count()
    total_periodo = periodo.count()
    cancelados = periodo.filter(status=PedidoVenda.Status.CANCELADO).count()
    clientes_recentes = base_filtrada.filter(
        status__in=STATUS_APROVADOS,
        data_emissao__gte=agora - timedelta(days=30),
    ).values('cliente_id')
    clientes_sem_compra = (
        Cliente.objects.for_filial(filial).filter(ativo=True)
        .exclude(pk__in=clientes_recentes).count()
    )

    return {
        'inicio': inicio,
        'fim': fim,
        'inicio_anterior': inicio_anterior,
        'fim_anterior': fim_anterior,
        'filtros': {
            'vendedor': vendedor_id,
            'regiao': regiao,
            'pagamento': pagamento_id,
        },
        'opcoes_vendedores': opcoes_vendedores,
        'opcoes_regioes': opcoes_regioes,
        'opcoes_pagamentos': opcoes_pagamentos,
        'resumo': resumo,
        'vendas': vendas,
        'pipeline': pipeline,
        'vendedores': vendedores,
        'regioes': regioes,
        'produtos': produtos,
        'pagamentos': pagamentos,
        'serie': _serie_temporal(periodo, inicio, fim),
        'alertas': {
            'analise_atrasada': analise_atrasada,
            'separacao_atrasada': separacao_atrasada,
            'clientes_sem_compra': clientes_sem_compra,
            'taxa_cancelamento': (
                Decimal(cancelados) / Decimal(total_periodo) * 100
                if total_periodo else ZERO
            ).quantize(Decimal('0.1')),
        },
    }
