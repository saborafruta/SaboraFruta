"""APIs de territórios (§11): listar polígonos, salvar desenho, indicadores."""
from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from apps.core.services.permissions import requer_permissao
from apps.mapas.services import TerritorioService


def _escopo(request):
    return TerritorioService._escopo_filiais(getattr(request, 'filial_ativa', None))


@require_GET
@requer_permissao('mapas', 'ver')
def territorios(request):
    """Polígonos das praças do escopo, para desenhar no mapa."""
    from apps.cadastros.models import Praca

    qs = (
        Praca.objects.filter(filial__in=_escopo(request), ativo=True)
        .exclude(poligono__isnull=True)
        .select_related('representante')
    )
    return JsonResponse({
        'territorios': [
            {
                'id': p.pk,
                'nome': p.nome,
                'codigo': p.codigo or '',
                'cor': p.cor or '#3b82f6',
                'poligono': p.poligono,
                'representante': getattr(p.representante, 'nome', '') or '',
                'supervisor': p.supervisor or '',
            }
            for p in qs
        ],
    })


@require_POST
@requer_permissao('mapas', 'editar')
def salvar_poligono(request, pk):
    """
    Grava o polígono desenhado no mapa.

    Recalcula a atribuição de clientes na mesma requisição: o desenho é um
    ato manual e pontual (não um lote), e ver o número de clientes do
    território mudar na hora é o feedback que valida o traçado.
    """
    from apps.cadastros.models import Praca

    praca = get_object_or_404(
        Praca.objects.filter(filial__in=_escopo(request)), pk=pk,
    )
    try:
        corpo = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'erro': 'JSON inválido.'}, status=400)

    pontos = corpo.get('poligono')
    if pontos is not None and not isinstance(pontos, list):
        return JsonResponse({'erro': 'poligono deve ser uma lista de pontos.'}, status=400)

    praca.definir_poligono(pontos)
    if pontos and not praca.tem_poligono:
        return JsonResponse(
            {'erro': 'Um polígono precisa de pelo menos 3 pontos válidos.'}, status=400,
        )

    praca.save(update_fields=[
        'poligono', 'bbox_sul', 'bbox_norte', 'bbox_oeste', 'bbox_leste', 'updated_at',
    ])
    total = TerritorioService.recalcular_praca(praca)
    return JsonResponse({
        'ok': True, 'id': praca.pk,
        'tem_poligono': praca.tem_poligono,
        'clientes': total,
    })


@require_GET
@requer_permissao('mapas', 'ver')
def indicadores_territorio(request, pk):
    """Painel do território ao clicar no polígono."""
    from apps.cadastros.models import Praca

    praca = get_object_or_404(
        Praca.objects.filter(filial__in=_escopo(request)).select_related('representante'),
        pk=pk,
    )
    try:
        dias = max(1, min(int(request.GET.get('dias', 30)), 365))
    except (TypeError, ValueError):
        dias = 30

    ind = TerritorioService.indicadores(praca, dias=dias)
    return JsonResponse({
        'id': praca.pk,
        'nome': praca.nome,
        'codigo': praca.codigo or '',
        'representante': getattr(praca.representante, 'nome', '') or '',
        'supervisor': praca.supervisor or '',
        'cidades': praca.lista_cidades,
        'clientes': ind['clientes'],
        'faturamento': float(ind['faturamento']),
        'pedidos': ind['pedidos'],
        'ticket_medio': float(ind['ticket_medio']),
        'meta': float(ind['meta']),
        'realizado_pct': ind['realizado_pct'],
        'dias': ind['dias'],
    })


@require_POST
@requer_permissao('mapas', 'editar')
def recalcular_territorios(request):
    """Recalcula a atribuição de todas as praças do escopo."""
    resultado = TerritorioService.recalcular_todas(getattr(request, 'filial_ativa', None))
    return JsonResponse({
        'ok': True,
        'pracas': len(resultado),
        'clientes_atribuidos': sum(v for v in resultado.values() if v > 0),
        'falhas': [pk for pk, v in resultado.items() if v < 0],
    })
