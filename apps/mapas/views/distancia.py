"""API de cálculo de distância entre cadastros (§6)."""
from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.core.services.permissions import requer_permissao
from apps.mapas.services.distancia import TIPOS, DistanciaService


@require_GET
@requer_permissao('mapas', 'ver')
def calcular_distancia(request):
    """
    GET /mapas/api/distancia/?de_tipo=cliente&de_id=1&para_tipo=filial&para_id=2

    Devolve distância, tempo e o traçado entre dois cadastros.
    """
    de_tipo = request.GET.get('de_tipo', '')
    para_tipo = request.GET.get('para_tipo', '')
    if de_tipo not in TIPOS or para_tipo not in TIPOS:
        return JsonResponse(
            {'erro': f'Tipos válidos: {", ".join(sorted(TIPOS))}.'}, status=400,
        )

    try:
        de_id = int(request.GET.get('de_id'))
        para_id = int(request.GET.get('para_id'))
    except (TypeError, ValueError):
        return JsonResponse({'erro': 'Informe de_id e para_id.'}, status=400)

    resultado = DistanciaService().calcular(
        filial=getattr(request, 'filial_ativa', None),
        origem_tipo=de_tipo, origem_id=de_id,
        destino_tipo=para_tipo, destino_id=para_id,
    )
    if resultado.get('erro'):
        return JsonResponse(resultado, status=400)
    return JsonResponse(resultado)


@require_GET
@requer_permissao('mapas', 'ver')
def buscar_destino(request):
    """
    GET /mapas/api/distancia/destinos/?tipo=filial&q=matriz

    Alimenta o seletor de destino do widget. Só devolve quem tem coordenada —
    oferecer um destino sem coordenada garantiria um erro no passo seguinte.
    """
    tipo = request.GET.get('tipo', '')
    if tipo not in TIPOS:
        return JsonResponse(
            {'erro': f'Tipos válidos: {", ".join(sorted(TIPOS))}.'}, status=400,
        )

    return JsonResponse({
        'tipo': tipo,
        'resultados': DistanciaService.buscar(
            getattr(request, 'filial_ativa', None), tipo,
            request.GET.get('q', '').strip(),
        ),
    })
