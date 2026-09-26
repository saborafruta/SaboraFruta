"""Painel público e móvel da rota atual do motoboy."""
import json
import re
from urllib.parse import urlencode

from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.pdv.models import RotaDelivery, VendaPDV


def _privado(response):
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _buscar_rota(token):
    if len(token) != 22:
        raise Http404
    return get_object_or_404(
        RotaDelivery._base_manager.select_related('filial'),
        token=token, ativa=True,
    )


def _sincronizar_rota_legada(rota):
    """Mantém links antigos coerentes durante a transição para múltiplas rotas."""
    from apps.pdv.models import RotaDeliveryPublica
    legada = RotaDeliveryPublica._base_manager.filter(
        filial=rota.filial, token=rota.token,
    ).first()
    if not legada:
        return
    legada.pedido_status_anteriores = rota.pedido_status_anteriores
    legada.paradas_extras_concluidas = rota.paradas_extras_concluidas
    legada.save(update_fields=[
        'pedido_status_anteriores', 'paradas_extras_concluidas', 'updated_at',
    ])


def _coordenada(objeto):
    return f'{float(objeto.latitude)},{float(objeto.longitude)}'


def _endereco_filial(filial):
    return ', '.join(filter(None, [
        filial.endereco,
        filial.numero,
        filial.bairro,
        filial.cidade,
        filial.uf,
    ])) or _coordenada(filial)


def _endereco_pedido(pedido):
    cliente = pedido.cliente
    endereco = pedido.endereco_entrega or {}
    return ', '.join(filter(None, [
        endereco.get('rua') or endereco.get('logradouro') or cliente.endereco,
        str(endereco.get('numero') or cliente.numero or ''),
        endereco.get('bairro') or cliente.bairro,
        endereco.get('cidade') or cliente.cidade,
        endereco.get('uf') or cliente.uf,
    ])) or _coordenada(cliente)


def _url_google_maps_completa(filial, paradas):
    """URL oficial única para comparar o comportamento do Maps no aparelho."""
    roteaveis = [parada for parada in paradas if parada.get('maps_location')]
    if not roteaveis or filial.latitude is None or filial.longitude is None:
        return ''
    base = _endereco_filial(filial)
    return 'https://www.google.com/maps/dir/?' + urlencode({
        'api': '1',
        'origin': base,
        'destination': base,
        'travelmode': 'driving',
        'waypoints': '|'.join(parada['maps_location'] for parada in roteaveis),
    })


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


def _dados_pedidos(pedidos, etas=None):
    from apps.financeiro.constants.enums import StatusContaReceber
    from apps.financeiro.models import ContaReceber

    nao_pagos = set(
        ContaReceber.objects.filter(
            documento_tipo='venda_pdv',
            documento_id__in=[pedido.pk for pedido in pedidos],
            status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
        ).values_list('documento_id', flat=True)
    )
    etas = etas if isinstance(etas, dict) else {}
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
        observacoes = list(dict.fromkeys(
            texto.strip() for texto in (pedido.observacao_delivery, pedido.observacao)
            if texto and texto.strip()
        ))
        dados.append({
            'tipo': 'pedido',
            'chave': f'pedido:{pedido.pk}',
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
            'eta': str(etas.get(str(pedido.pk)) or ''),
            # Venda aberta/orcamento e apenas um rascunho: nao existe
            # pagamento real ainda, mesmo que nenhuma conta a receber tenha
            # sido criada. So uma venda finalizada pode aparecer como paga.
            'pago': pedido.status == 'finalizada' and pedido.pk not in nao_pagos,
            'pagamento_pendente': pedido.status != 'finalizada',
            'formas_pagamento': ', '.join(
                pagamento.forma_pagamento.descricao
                for pagamento in pedido.pagamentos.all()
                if pagamento.forma_pagamento
            ) or 'Não informado',
            'concluido': pedido.status_delivery in (
                VendaPDV.StatusDelivery.ENTREGUE,
                VendaPDV.StatusDelivery.FINALIZADO,
            ),
            'pode_alterar': pedido.status_delivery not in (
                VendaPDV.StatusDelivery.FINALIZADO,
                VendaPDV.StatusDelivery.CANCELADO,
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
            'maps_location': _endereco_pedido(pedido),
        })
    # Mantém o número original da parada, mas leva as concluídas para o fim.
    return sorted(dados, key=lambda item: (item['concluido'], item['ordem']))


def _dados_paradas_rota(rota, pedidos):
    entregas = _dados_pedidos(pedidos, rota.pedido_etas)
    por_chave = {item['chave']: item for item in entregas}
    concluidas = {str(item) for item in (rota.paradas_extras_concluidas or [])}
    for extra in rota.paradas_extras or []:
        if not isinstance(extra, dict) or not extra.get('id'):
            continue
        identificador = str(extra['id'])
        chave = f'manual:{identificador}'
        endereco = extra.get('endereco') if isinstance(extra.get('endereco'), dict) else {}
        endereco_texto = str(extra.get('endereco_texto') or '').strip() or ', '.join(filter(None, [
            endereco.get('rua'), endereco.get('numero'), endereco.get('bairro'),
            endereco.get('cidade'), endereco.get('uf'),
        ]))
        try:
            maps_location = f"{float(extra['lat'])},{float(extra['lng'])}"
        except (KeyError, TypeError, ValueError):
            maps_location = endereco_texto
        por_chave[chave] = {
            'tipo': 'manual', 'chave': chave, 'manual_id': identificador,
            'origem': str(extra.get('origem') or 'manual'),
            'cliente': str(extra.get('observacao') or 'Parada manual'),
            'endereco': endereco_texto, 'complemento': '',
            'telefone': str(extra.get('telefone') or ''),
            'whatsapp': str(extra.get('whatsapp') or ''),
            'rfm': str(extra.get('rfm') or ''),
            'segmento_rfm': str(extra.get('segmento_rfm') or ''),
            'observacoes': [], 'eta': str(extra.get('eta') or ''), 'pago': False,
            'pagamento_pendente': False, 'formas_pagamento': '',
            'concluido': identificador in concluidas, 'pode_alterar': True,
            'navegar_url': 'https://www.google.com/maps/dir/?' + urlencode({
                'api': '1', 'destination': maps_location, 'travelmode': 'driving',
                'dir_action': 'navigate',
            }),
            'maps_location': maps_location,
        }
    ordem = [str(chave) for chave in (rota.ordem_paradas or []) if str(chave) in por_chave]
    ordem.extend(chave for chave in por_chave if chave not in ordem)
    paradas = []
    for numero, chave in enumerate(ordem, start=1):
        item = por_chave[chave]
        item['ordem'] = numero
        paradas.append(item)
    return sorted(paradas, key=lambda item: (item['concluido'], item['ordem']))


@require_GET
def painel(request, token):
    rota = _buscar_rota(token)
    pedidos = _pedidos_da_rota(rota)
    dados = _dados_paradas_rota(rota, pedidos)
    response = render(request, 'pdv/delivery_motorista_publico.html', {
        'rota': rota,
        'pedidos': dados,
        'total': len(dados),
        'concluidos': sum(1 for pedido in dados if pedido['concluido']),
        'google_maps_completa': _url_google_maps_completa(rota.filial, dados),
    })
    return _privado(response)


@require_POST
def concluir(request, token, pk):
    rota = _buscar_rota(token)
    ids = {int(item) for item in rota.pedido_ids if str(item).isdigit()}
    if pk not in ids:
        raise Http404
    entregue = True
    if request.content_type == 'application/json':
        try:
            corpo = json.loads(request.body or b'{}')
        except ValueError:
            return _privado(JsonResponse({'erro': 'JSON inválido.'}, status=400))
        entregue = corpo.get('entregue', True) is not False

    with transaction.atomic():
        rota = get_object_or_404(
            RotaDelivery._base_manager.select_for_update(),
            pk=rota.pk, ativa=True,
        )
        venda = get_object_or_404(
            VendaPDV._base_manager.select_for_update(),
            pk=pk, filial=rota.filial, delivery=True,
        )
        if venda.status_delivery in (
            VendaPDV.StatusDelivery.FINALIZADO,
            VendaPDV.StatusDelivery.CANCELADO,
        ):
            return _privado(JsonResponse({
                'erro': 'Esta entrega já foi encerrada e não pode ser alterada.'
            }, status=400))
        anteriores = dict(rota.pedido_status_anteriores or {})
        chave = str(pk)
        if entregue:
            if venda.status_delivery != VendaPDV.StatusDelivery.ENTREGUE:
                anteriores[chave] = venda.status_delivery
            novo_status = VendaPDV.StatusDelivery.ENTREGUE
        else:
            novo_status = anteriores.pop(
                chave, VendaPDV.StatusDelivery.EM_ENTREGA,
            )
            if novo_status not in (
                VendaPDV.StatusDelivery.NOVO,
                VendaPDV.StatusDelivery.PREPARANDO,
                VendaPDV.StatusDelivery.EM_ENTREGA,
            ):
                novo_status = VendaPDV.StatusDelivery.EM_ENTREGA
        if venda.status_delivery != novo_status:
            venda.mudar_status_delivery(novo_status)
        rota.pedido_status_anteriores = anteriores
        rota.save(update_fields=['pedido_status_anteriores', 'updated_at'])
        _sincronizar_rota_legada(rota)
    if 'application/json' in request.headers.get('Accept', ''):
        return _privado(JsonResponse({
            'ok': True,
            'pedido_id': pk,
            'status': novo_status,
            'status_label': venda.get_status_delivery_display(),
            'concluido': entregue,
        }))
    return redirect(reverse('delivery_publico:painel', args=[token]) + f'#pedido-{pk}')


@require_POST
def concluir_parada_extra(request, token, parada_id):
    rota = _buscar_rota(token)
    extras = {
        str(item.get('id')) for item in (rota.paradas_extras or [])
        if isinstance(item, dict) and item.get('id')
    }
    if parada_id not in extras:
        raise Http404
    concluida = True
    if request.content_type == 'application/json':
        try:
            corpo = json.loads(request.body or b'{}')
        except ValueError:
            return _privado(JsonResponse({'erro': 'JSON inválido.'}, status=400))
        concluida = corpo.get('entregue', True) is not False
    with transaction.atomic():
        rota = get_object_or_404(
            RotaDelivery._base_manager.select_for_update(), pk=rota.pk, ativa=True,
        )
        concluidas = {str(item) for item in (rota.paradas_extras_concluidas or [])}
        if concluida:
            concluidas.add(parada_id)
        else:
            concluidas.discard(parada_id)
        rota.paradas_extras_concluidas = sorted(concluidas)
        rota.save(update_fields=['paradas_extras_concluidas', 'updated_at'])
        _sincronizar_rota_legada(rota)
    if 'application/json' in request.headers.get('Accept', ''):
        return _privado(JsonResponse({'ok': True, 'concluido': concluida}))
    return redirect(reverse('delivery_publico:painel', args=[token]) + f'#parada-{parada_id}')
