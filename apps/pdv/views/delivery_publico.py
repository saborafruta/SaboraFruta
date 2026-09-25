"""Painel público e móvel da rota atual do motoboy."""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.pdv.models import RotaDeliveryPublica, VendaPDV


def _privado(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _buscar_rota(token):
    if len(token) != 22:
        raise Http404
    return get_object_or_404(
        RotaDeliveryPublica._base_manager.select_related('filial'),
        token=token, ativa=True,
    )


def _coordenada(objeto):
    return f'{float(objeto.latitude)},{float(objeto.longitude)}'


def _urls_google_maps(filial, pedidos):
    """Divide em até três entregas por link, limite seguro no Maps móvel."""
    roteaveis = [
        pedido for pedido in pedidos
        if pedido.cliente and pedido.cliente.latitude is not None
        and pedido.cliente.longitude is not None
    ]
    if not roteaveis or filial.latitude is None or filial.longitude is None:
        return []

    origem_filial = _coordenada(filial)
    grupos = [roteaveis[i:i + 3] for i in range(0, len(roteaveis), 3)]
    urls = []
    for indice, grupo in enumerate(grupos):
        origem = origem_filial if indice == 0 else _coordenada(grupos[indice - 1][-1].cliente)
        ultima_etapa = indice == len(grupos) - 1
        destino = origem_filial if ultima_etapa else _coordenada(grupo[-1].cliente)
        intermediarios = grupo if ultima_etapa else grupo[:-1]
        parametros = {
            'api': '1',
            'origin': origem,
            'destination': destino,
            'travelmode': 'driving',
            'dir_action': 'navigate',
        }
        if intermediarios:
            parametros['waypoints'] = '|'.join(_coordenada(p.cliente) for p in intermediarios)
        urls.append('https://www.google.com/maps/dir/?' + urlencode(parametros))
    return urls


def _url_google_maps_completa(filial, pedidos):
    """URL oficial única para comparar o comportamento do Maps no aparelho."""
    roteaveis = [
        pedido for pedido in pedidos
        if pedido.cliente and pedido.cliente.latitude is not None
        and pedido.cliente.longitude is not None
    ]
    if not roteaveis or filial.latitude is None or filial.longitude is None:
        return ''
    base = _coordenada(filial)
    return 'https://www.google.com/maps/dir/?' + urlencode({
        'api': '1',
        'origin': base,
        'destination': base,
        'travelmode': 'driving',
        'dir_action': 'navigate',
        'waypoints': '|'.join(_coordenada(pedido.cliente) for pedido in roteaveis),
    })


def _url_osmand(filial, pedidos):
    """Rota completa no planejador oficial do OsmAnd, sem dividir paradas."""
    roteaveis = [
        pedido for pedido in pedidos
        if pedido.cliente and pedido.cliente.latitude is not None
        and pedido.cliente.longitude is not None
    ]
    if not roteaveis or filial.latitude is None or filial.longitude is None:
        return ''
    base = _coordenada(filial)
    parametros = [
        ('start', base),
        ('finish', base),
        *[('via', _coordenada(pedido.cliente)) for pedido in roteaveis],
        ('type', 'osmand'),
        ('profile', 'car'),
    ]
    return 'https://osmand.net/map/?' + urlencode(parametros)


def _telefone_whatsapp(cliente):
    if cliente is None:
        return '', ''
    exibicao = (cliente.celular or cliente.telefone or '').strip()
    digitos = re.sub(r'\D', '', exibicao)
    if len(digitos) in (10, 11):
        digitos = '55' + digitos
    return exibicao, digitos if 12 <= len(digitos) <= 13 else ''


def _pedidos_da_rota(rota):
    ids = [int(pk) for pk in rota.pedido_ids if str(pk).isdigit()]
    encontrados = {
        venda.pk: venda
        for venda in (
            VendaPDV._base_manager
            .filter(pk__in=ids, filial=rota.filial, delivery=True)
            .exclude(status='cancelada')
            .select_related('cliente')
            .prefetch_related('itens__produto', 'pagamentos__forma_pagamento')
        )
    }
    return [encontrados[pk] for pk in ids if pk in encontrados]


def _dados_pedidos(pedidos):
    from apps.financeiro.constants.enums import StatusContaReceber
    from apps.financeiro.models import ContaReceber

    nao_pagos = set(
        ContaReceber.objects.filter(
            documento_tipo='venda_pdv',
            documento_id__in=[pedido.pk for pedido in pedidos],
            status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
        ).values_list('documento_id', flat=True)
    )
    dados = []
    for ordem, pedido in enumerate(pedidos, start=1):
        cliente = pedido.cliente
        telefone, whatsapp = _telefone_whatsapp(cliente)
        endereco = pedido.endereco_entrega or {}
        endereco_texto = ', '.join(filter(None, [
            endereco.get('rua') or endereco.get('logradouro') or (cliente.endereco if cliente else ''),
            str(endereco.get('numero') or (cliente.numero if cliente else '') or ''),
            endereco.get('bairro') or (cliente.bairro if cliente else ''),
        ]))
        observacoes = [
            texto.strip() for texto in (pedido.observacao_delivery, pedido.observacao)
            if texto and texto.strip()
        ]
        dados.append({
            'ordem': ordem,
            'venda': pedido,
            'cliente': (
                (cliente.nome_fantasia or cliente.razao_social)
                if cliente else 'Consumidor Final'
            ),
            'endereco': endereco_texto,
            'complemento': endereco.get('complemento') or '',
            'telefone': telefone,
            'whatsapp': whatsapp,
            'observacoes': observacoes,
            'pago': pedido.pk not in nao_pagos,
            'formas_pagamento': ', '.join(
                pagamento.forma_pagamento.descricao
                for pagamento in pedido.pagamentos.all()
                if pagamento.forma_pagamento
            ) or 'Não informado',
            'concluido': pedido.status_delivery in (
                VendaPDV.StatusDelivery.ENTREGUE,
                VendaPDV.StatusDelivery.FINALIZADO,
            ),
            'navegar_url': (
                'https://www.google.com/maps/dir/?' + urlencode({
                    'api': '1',
                    'destination': _coordenada(cliente),
                    'travelmode': 'driving',
                    'dir_action': 'navigate',
                })
                if cliente and cliente.latitude is not None and cliente.longitude is not None
                else ''
            ),
        })
    return dados


@require_GET
def painel(request, token):
    rota = _buscar_rota(token)
    pedidos = _pedidos_da_rota(rota)
    dados = _dados_pedidos(pedidos)
    response = render(request, 'pdv/delivery_motorista_publico.html', {
        'rota': rota,
        'pedidos': dados,
        'total': len(dados),
        'concluidos': sum(1 for pedido in dados if pedido['concluido']),
        'etapas_maps': _urls_google_maps(rota.filial, pedidos),
        'google_maps_completa': _url_google_maps_completa(rota.filial, pedidos),
        'osmand_url': _url_osmand(rota.filial, pedidos),
    })
    return _privado(response)


@require_GET
def gpx(request, token):
    """Entrega a rota publicada como GPX para importação no OsmAnd."""
    rota = _buscar_rota(token)
    pedidos = _pedidos_da_rota(rota)
    filial = rota.filial
    if filial.latitude is None or filial.longitude is None:
        raise Http404

    namespace = 'http://www.topografix.com/GPX/1/1'
    ET.register_namespace('', namespace)
    raiz = ET.Element(f'{{{namespace}}}gpx', {
        'version': '1.1',
        'creator': 'Saborafruta',
    })
    metadata = ET.SubElement(raiz, f'{{{namespace}}}metadata')
    ET.SubElement(metadata, f'{{{namespace}}}name').text = 'Rota do Delivery'
    caminho = ET.SubElement(raiz, f'{{{namespace}}}rte')
    ET.SubElement(caminho, f'{{{namespace}}}name').text = 'Rota do Delivery'

    pontos = [(filial, f'Saída — {filial.nome_fantasia or filial.razao_social}')]
    for ordem, pedido in enumerate(pedidos, start=1):
        cliente = pedido.cliente
        if cliente and cliente.latitude is not None and cliente.longitude is not None:
            nome = cliente.nome_fantasia or cliente.razao_social or f'Pedido {pedido.numero_venda}'
            pontos.append((cliente, f'{ordem}. #{pedido.numero_venda} — {nome}'))
    pontos.append((filial, f'Retorno — {filial.nome_fantasia or filial.razao_social}'))

    for ponto, nome in pontos:
        elemento = ET.SubElement(caminho, f'{{{namespace}}}rtept', {
            'lat': str(ponto.latitude),
            'lon': str(ponto.longitude),
        })
        ET.SubElement(elemento, f'{{{namespace}}}name').text = nome

    response = HttpResponse(
        ET.tostring(raiz, encoding='utf-8', xml_declaration=True),
        content_type='application/gpx+xml',
    )
    response['Content-Disposition'] = 'attachment; filename="rota-delivery.gpx"'
    return _privado(response)


@require_POST
def concluir(request, token, pk):
    rota = _buscar_rota(token)
    ids = {int(item) for item in rota.pedido_ids if str(item).isdigit()}
    if pk not in ids:
        raise Http404
    with transaction.atomic():
        venda = get_object_or_404(
            VendaPDV._base_manager.select_for_update(),
            pk=pk, filial=rota.filial, delivery=True,
        )
        if venda.status_delivery not in (
            VendaPDV.StatusDelivery.ENTREGUE,
            VendaPDV.StatusDelivery.FINALIZADO,
            VendaPDV.StatusDelivery.CANCELADO,
        ):
            venda.mudar_status_delivery(VendaPDV.StatusDelivery.ENTREGUE)
    return redirect(reverse('delivery_publico:painel', args=[token]) + f'#pedido-{pk}')
