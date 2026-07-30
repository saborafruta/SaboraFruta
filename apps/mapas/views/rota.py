"""API de roteirização (§4): monta a rota dos clientes selecionados no mapa."""
from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.core.services.permissions import requer_permissao
from apps.mapas.services.roteirizacao import MAX_PARADAS, RoteirizacaoService


@require_POST
@requer_permissao('mapas', 'ver')
def criar_rota(request):
    """
    POST /mapas/api/rota/  {"clientes": [12, 8, 45], "partir_da_filial": true}

    Devolve distância total, tempo estimado, o traçado para desenhar no mapa e
    a lista de paradas na ordem de visita.
    """
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)

    brutos = corpo.get('clientes')
    if not isinstance(brutos, list):
        return JsonResponse({'erro': 'Envie a lista de clientes.'}, status=400)

    # Mantém a ordem escolhida e remove repetidos: clicar duas vezes no mesmo
    # pino não deve criar uma parada duplicada no mesmo lugar.
    vistos, cliente_ids = set(), []
    for bruto in brutos:
        try:
            cid = int(bruto)
        except (TypeError, ValueError):
            continue
        if cid not in vistos:
            vistos.add(cid)
            cliente_ids.append(cid)

    servico = RoteirizacaoService()
    rota = servico.rota_de_clientes(
        filial=getattr(request, 'filial_ativa', None),
        cliente_ids=cliente_ids,
        partir_da_filial=bool(corpo.get('partir_da_filial', True)),
    )

    if not rota.ok:
        return JsonResponse(
            {'erro': rota.erro or 'Não foi possível calcular a rota.'}, status=400,
        )

    return JsonResponse({
        'distancia_m': round(rota.distancia_m),
        'distancia_km': rota.distancia_km,
        'duracao_s': round(rota.duracao_s),
        'duracao_texto': rota.duracao_texto,
        'geometria': rota.geometria,
        'provider': servico.roteirizador.nome,
        'uso_comercial_liberado': servico.roteirizador.permite_uso_comercial,
        'max_paradas': MAX_PARADAS,
        'paradas': [
            {
                'ordem': p.ordem,
                'nome': p.nome,
                'lat': p.lat,
                'lng': p.lng,
                'cliente_id': p.cliente_id,
            }
            for p in rota.paradas
        ],
    })
