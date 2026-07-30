"""API de roteirização (§4): monta a rota dos clientes selecionados no mapa."""
from __future__ import annotations

import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.core.services.permissions import requer_permissao
from apps.mapas.services.otimizacao import OtimizacaoService
from apps.mapas.services.roteirizacao import MAX_PARADAS, RoteirizacaoService


def _ids_do_corpo(corpo):
    """
    Ids de cliente na ordem recebida, sem repetidos.

    Clicar duas vezes no mesmo pino não deve virar duas paradas no mesmo lugar.
    """
    vistos, ids = set(), []
    for bruto in (corpo.get('clientes') or []):
        try:
            cid = int(bruto)
        except (TypeError, ValueError):
            continue
        if cid not in vistos:
            vistos.add(cid)
            ids.append(cid)
    return ids


def _serializar_rota(rota):
    return {
        'distancia_m': round(rota.distancia_m),
        'distancia_km': rota.distancia_km,
        'duracao_s': round(rota.duracao_s),
        'duracao_texto': rota.duracao_texto,
        'geometria': rota.geometria,
        'paradas': [
            {
                'ordem': p.ordem, 'nome': p.nome, 'lat': p.lat, 'lng': p.lng,
                'cliente_id': p.cliente_id,
            }
            for p in rota.paradas
        ],
    }


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

    if not isinstance(corpo.get('clientes'), list):
        return JsonResponse({'erro': 'Envie a lista de clientes.'}, status=400)
    cliente_ids = _ids_do_corpo(corpo)

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
        **_serializar_rota(rota),
        'provider': servico.roteirizador.nome,
        'uso_comercial_liberado': servico.roteirizador.permite_uso_comercial,
        'max_paradas': MAX_PARADAS,
    })


@require_POST
@requer_permissao('mapas', 'ver')
def otimizar_rota(request):
    """
    POST /mapas/api/rota/otimizar/  {"clientes": [...], "partir_da_filial": true}

    Reordena as entregas e devolve o antes, o depois e o quanto se economiza.
    """
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)

    if not isinstance(corpo.get('clientes'), list):
        return JsonResponse({'erro': 'Envie a lista de clientes.'}, status=400)

    servico = OtimizacaoService()
    resultado = servico.otimizar(
        filial=getattr(request, 'filial_ativa', None),
        cliente_ids=_ids_do_corpo(corpo),
        partir_da_filial=bool(corpo.get('partir_da_filial', True)),
    )
    if not resultado.ok:
        return JsonResponse(
            {'erro': resultado.erro or 'Não foi possível otimizar.'}, status=400,
        )

    return JsonResponse({
        'estrategia': resultado.estrategia,
        'melhorou': resultado.melhorou,
        'economia_km': resultado.economia_km,
        'economia_texto': resultado.economia_texto,
        'antes': _serializar_rota(resultado.rota_antes),
        'depois': _serializar_rota(resultado.rota_depois),
        'ordem_depois': resultado.ordem_depois,
    })
