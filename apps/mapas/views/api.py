"""
APIs REST do módulo de Mapas.

Autenticação/permissão: usam o mesmo `requer_permissao` do resto do sistema
(módulo `mapas`), e não as classes do DRF, porque o mapa é consumido pela
sessão do próprio ERP — não por um cliente externo com JWT. Assim o escopo de
filial de `request.filial_ativa` continua valendo, o que é essencial num SaaS
multiempresa: sem isso um usuário veria pinos de outra empresa.
"""
from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.core.services.permissions import requer_permissao
from apps.mapas import constants as c
from apps.mapas.managers import apenas_geocodificados, limitar_marcadores, na_area
from apps.mapas.serializers import serializar_cliente_proximo
from apps.mapas.services import ProximidadeService


def _escopo(request):
    return ProximidadeService._escopo_filiais(getattr(request, 'filial_ativa', None))


def _float(valor, default=None):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────── camadas (marcadores)
def _marcadores_clientes(request, filiais):
    """
    Clientes geocodificados, coloridos pelo status de recompra (§9).

    O join com RecompraCliente é feito por dicionário em uma query só; um
    select_related não serve porque a relação é inversa e nem todo cliente
    tem registro de recompra.
    """
    from apps.cadastros.models import Cliente

    qs = apenas_geocodificados(
        Cliente.objects.filter(filial__in=filiais, ativo=True)
    ).only('id', 'razao_social', 'nome_fantasia', 'cidade', 'latitude', 'longitude')

    qs = _recortar_viewport(request, qs)
    clientes, truncado = limitar_marcadores(qs)

    status_por_cliente = {}
    if clientes:
        try:
            from apps.crm.models import RecompraCliente

            status_por_cliente = dict(
                RecompraCliente.objects
                .filter(cliente_id__in=[cl.pk for cl in clientes], filial__in=filiais)
                .values_list('cliente_id', 'status')
            )
        except Exception:  # pragma: no cover - CRM é opcional aqui
            status_por_cliente = {}

    cor_camada = c.CAMADAS['clientes'][1]
    marcadores = [
        {
            'id': cl.pk,
            'nome': cl.nome_fantasia or cl.razao_social or f'Cliente {cl.pk}',
            'lat': cl.latitude,
            'lng': cl.longitude,
            'cidade': cl.cidade or '',
            'cor': c.CORES_STATUS_RECOMPRA.get(
                status_por_cliente.get(cl.pk, ''), cor_camada,
            ),
        }
        for cl in clientes
    ]
    return marcadores, truncado


def _marcadores_simples(request, qs, campo_nome, cor):
    """Camadas sem enriquecimento: fornecedores, filiais, motoristas etc."""
    qs = _recortar_viewport(request, apenas_geocodificados(qs))
    itens, truncado = limitar_marcadores(qs)
    marcadores = [
        {
            'id': obj.pk,
            'nome': getattr(obj, campo_nome, None) or str(obj),
            'lat': obj.latitude,
            'lng': obj.longitude,
            'cidade': getattr(obj, 'cidade', '') or '',
            'cor': cor,
        }
        for obj in itens
    ]
    return marcadores, truncado


def _recortar_viewport(request, qs):
    """Aplica o bounding box da viewport, se o front mandou (lazy loading §16)."""
    sul, oeste = _float(request.GET.get('sul')), _float(request.GET.get('oeste'))
    norte, leste = _float(request.GET.get('norte')), _float(request.GET.get('leste'))
    if None in (sul, oeste, norte, leste):
        return qs
    return na_area(qs, sul, oeste, norte, leste)


@require_GET
@requer_permissao('mapas', 'ver')
def camadas(request):
    """
    Marcadores de uma ou mais camadas.

    `?camadas=clientes,filiais` — o front pede só o que está ligado no menu,
    em vez de baixar tudo e esconder no cliente.
    """
    from apps.cadastros.models import Fornecedor, Motorista, Transportadora
    from apps.core.models import Filial

    filiais = _escopo(request)
    pedidas = [x for x in (request.GET.get('camadas') or 'clientes').split(',') if x]

    resultado, truncadas = {}, []
    for chave in pedidas:
        if chave not in c.CAMADAS:
            continue
        cor = c.CAMADAS[chave][1]

        if chave == 'clientes':
            marcadores, truncado = _marcadores_clientes(request, filiais)
        elif chave == 'fornecedores':
            marcadores, truncado = _marcadores_simples(
                request, Fornecedor.objects.filter(filial__in=filiais, ativo=True),
                'razao_social', cor,
            )
        elif chave == 'filiais':
            marcadores, truncado = _marcadores_simples(
                request, Filial.objects.filter(pk__in=filiais.values('pk')),
                'nome_fantasia', cor,
            )
        elif chave == 'motoristas':
            marcadores, truncado = _marcadores_simples(
                request, Motorista.objects.filter(filial__in=filiais, ativo=True),
                'nome', cor,
            )
        elif chave == 'transportadoras':
            marcadores, truncado = _marcadores_simples(
                request, Transportadora.objects.filter(filial__in=filiais, ativo=True),
                'razao_social', cor,
            )
        else:
            continue

        resultado[chave] = marcadores
        if truncado:
            truncadas.append(chave)

    return JsonResponse({
        'camadas': resultado,
        'truncadas': truncadas,
        'limite': c.LIMITE_MARCADORES,
    })


# ─────────────────────────────────────────────── clientes próximos (§7)
@require_GET
@requer_permissao('mapas', 'ver')
def clientes_proximos(request):
    """
    GET /mapas/api/clientes-proximos/?lat=&lng=&raio=
    Devolve os clientes ordenados por proximidade.
    """
    lat, lng = _float(request.GET.get('lat')), _float(request.GET.get('lng'))
    if lat is None or lng is None:
        return JsonResponse({'erro': 'Informe lat e lng.'}, status=400)
    if not c.dentro_do_brasil(lat, lng):
        return JsonResponse({'erro': 'Coordenada fora do Brasil.'}, status=400)

    raio = int(_float(request.GET.get('raio'), c.RAIO_PADRAO_M))
    excluir = request.GET.get('excluir_cliente') or None

    clientes = ProximidadeService.clientes_proximos(
        filial=getattr(request, 'filial_ativa', None),
        latitude=lat, longitude=lng, raio_m=raio,
        excluir_cliente_id=excluir,
    )
    return JsonResponse({
        'centro': {'lat': lat, 'lng': lng},
        'raio_m': min(max(raio, 1), c.RAIO_MAXIMO_M),
        'total': len(clientes),
        'clientes': [serializar_cliente_proximo(cl) for cl in clientes],
    })


# ─────────────────────────────────────────────── sugestão ao entregar (§8)
@require_GET
@requer_permissao('pdv', 'ver')
def sugestao_entrega(request, pk):
    """
    GET /mapas/api/sugestao-entrega/<pk>/?raio=

    Clientes perto do endereço de entrega de um pedido de delivery.

    A permissão exigida é a do **PDV**, não a de mapas: quem consome isto é o
    Kanban de delivery, e um operador de balcão costuma não ter acesso ao
    módulo de mapas. Exigir `mapas.ver` esconderia a sugestão justamente de
    quem está com o pedido na mão. O dado exposto (clientes da própria filial)
    esse operador já alcança pela busca do PDV.
    """
    from apps.pdv.models import VendaPDV

    venda = (
        VendaPDV.objects
        .filter(pk=pk, delivery=True, filial__in=_escopo(request))
        .select_related('cliente')
        .first()
    )
    if venda is None:
        return JsonResponse({'erro': 'Pedido não encontrado.'}, status=404)

    raio = int(_float(request.GET.get('raio'), c.RAIO_PADRAO_M))
    lat, lng, clientes = ProximidadeService.proximos_de_entrega(venda, raio_m=raio)

    # Sem coordenada não há sugestão possível. Devolver 200 com o motivo (em
    # vez de erro) deixa a tela explicar o que fazer: um 4xx viraria só um
    # "falhou" genérico para quem só precisa geocodificar o cliente.
    if lat is None:
        return JsonResponse({
            'venda_id': venda.pk, 'centro': None, 'total': 0, 'clientes': [],
            'motivo': 'Este pedido não tem coordenada de entrega. '
                      'Geocodifique o endereço do cliente para ver sugestões.',
        })

    _registrar_sugestao(venda, raio, len(clientes))

    return JsonResponse({
        'venda_id': venda.pk,
        'centro': {'lat': lat, 'lng': lng},
        'raio_m': min(max(raio, 1), c.RAIO_MAXIMO_M),
        'total': len(clientes),
        'clientes': [serializar_cliente_proximo(cl) for cl in clientes],
    })


def _registrar_sugestao(venda, raio, total):
    """
    Grava a sugestão oferecida, para o indicador do §14.

    Como no registro de rota, uma falha aqui não pode tirar a sugestão da tela:
    o log serve a um número no painel, a sugestão serve à venda.
    """
    import logging

    from apps.mapas.models import SugestaoProximidade

    try:
        SugestaoProximidade.objects.create(
            filial=venda.filial, venda_pdv_id=venda.pk,
            raio_m=raio, total=total,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            'falha ao registrar sugestão de proximidade')


# ─────────────────────────────────────────────── detalhe do popup (§3)
@require_GET
@requer_permissao('mapas', 'ver')
def cliente_detalhe(request, pk):
    """
    Dados ricos do popup de um cliente — buscados só ao clicar no pino, para
    não inflar o payload de milhares de marcadores.
    """
    from apps.cadastros.models import Cliente

    cliente = Cliente.objects.filter(pk=pk, filial__in=_escopo(request)).first()
    if cliente is None:
        return JsonResponse({'erro': 'Cliente não encontrado.'}, status=404)

    dados = {
        'id': cliente.pk,
        'nome': cliente.nome_fantasia or cliente.razao_social,
        'razao_social': cliente.razao_social,
        'cpf_cnpj': cliente.cpf_cnpj or '',
        'telefone': cliente.celular or cliente.telefone or '',
        'cidade': cliente.cidade or '',
        'uf': cliente.uf or '',
        'bairro': cliente.bairro or '',
        'limite_credito': _limite_credito(cliente),
        'lat': cliente.latitude,
        'lng': cliente.longitude,
        'geo_precisao': cliente.geo_precisao,
    }
    dados.update(_indicadores_comerciais(cliente, _escopo(request)))
    return JsonResponse(dados)


def _limite_credito(cliente):
    valor = getattr(cliente, 'limite_credito', None)
    return float(valor) if valor is not None else None


def _indicadores_comerciais(cliente, filiais) -> dict:
    """
    Última compra, valor no mês e dias sem comprar.

    Reaproveita o `RecompraCliente` do CRM para última compra/valor médio (já
    calculado e indexado) e soma o mês corrente direto no PDV. Recalcular o
    histórico aqui seria duplicar o serviço de recompra.
    """
    from datetime import date

    from django.db.models import Sum

    from apps.pdv.models import VendaPDV

    saida = {
        'ultima_compra': None, 'dias_sem_comprar': None,
        'valor_medio': None, 'frequencia': '', 'status_recompra': '',
        'representante': '', 'valor_mes': 0.0,
    }

    try:
        from apps.crm.models import RecompraCliente

        # `representante` vem daqui e não do cadastro: Cliente não tem esse
        # campo: o serviço de recompra materializa o representante do pedido
        # mais recente. Buscar de outra fonte faria o mapa mostrar um nome
        # diferente do que a tela de CRM mostra para o mesmo cliente.
        r = (
            RecompraCliente.objects
            .filter(cliente=cliente, filial__in=filiais)
            .select_related('representante')
            .first()
        )
        if r is not None:
            saida.update({
                'ultima_compra': r.ultima_compra.isoformat() if r.ultima_compra else None,
                'dias_sem_comprar': r.dias_desde_ultima_compra,
                'valor_medio': float(r.valor_medio or 0),
                'frequencia': r.get_frequencia_display(),
                'status_recompra': r.status,
                'representante': getattr(r.representante, 'nome', '') or '',
            })
    except Exception:  # pragma: no cover
        pass

    hoje = date.today()
    vendas_mes = VendaPDV.objects.filter(
        cliente=cliente, filial__in=filiais, status='finalizada',
        data_venda__date__gte=hoje.replace(day=1),
    )
    # Doacao/Permuta nao sao receita: o popup mostra o que o cliente
    # efetivamente pagou no mes.
    from apps.financeiro.services.receita import ajuste_total

    total = vendas_mes.aggregate(t=Sum('valor_total'))['t'] or 0
    saida['valor_mes'] = float(max(0, total - ajuste_total(vendas_mes)))
    return saida
