"""API do mapa de calor de vendas (§10)."""
from __future__ import annotations

import datetime

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from apps.core.services.permissions import requer_permissao
from apps.mapas.services.heatmap import METRICAS, HeatmapService


def _data(valor):
    """`YYYY-MM-DD` -> date. Data inválida vira None (usa o padrão)."""
    try:
        return datetime.date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


@require_GET
@requer_permissao('mapas', 'ver')
def heatmap(request):
    """
    GET /mapas/api/heatmap/?metrica=receita&de=&ate=&cidade=&uf=&representante=&filial=

    Métricas: receita, pedidos, volume, clientes.
    """
    return JsonResponse(HeatmapService.pontos(
        filial=getattr(request, 'filial_ativa', None),
        metrica=request.GET.get('metrica', 'receita'),
        inicio=_data(request.GET.get('de')),
        fim=_data(request.GET.get('ate')),
        cidade=request.GET.get('cidade', '').strip(),
        uf=request.GET.get('uf', '').strip(),
        representante_id=_inteiro(request.GET.get('representante')),
        filial_id=_inteiro(request.GET.get('filial')),
    ))


@require_GET
@requer_permissao('mapas', 'ver')
def heatmap_filtros(request):
    """
    GET /mapas/api/heatmap/filtros/

    Alimenta os seletores. Só devolve o que existe no escopo do usuário —
    listar cidades ou representantes de outra empresa já seria vazamento,
    mesmo sem nenhum número junto.
    """
    filial = getattr(request, 'filial_ativa', None)
    if filial is None:
        return JsonResponse({'cidades': [], 'ufs': [],
                             'representantes': [], 'filiais': [],
                             'metricas': []})

    dados = HeatmapService.opcoes_de_filtro(filial)
    dados['metricas'] = [
        {'chave': k, 'rotulo': v[0], 'unidade': v[1]} for k, v in METRICAS.items()
    ]
    return JsonResponse(dados)
