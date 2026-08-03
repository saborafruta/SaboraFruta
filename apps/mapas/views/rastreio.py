"""Mapa ao vivo dos motoristas (§13)."""
from __future__ import annotations

import datetime

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from apps.core.services.permissions import PermissaoRequiredMixin, requer_permissao
from apps.mapas.services.rastreio import LIMITE_ONLINE_S, RastreioService


def _data(valor):
    try:
        return datetime.date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


class MapaAoVivoView(PermissaoRequiredMixin, TemplateView):
    """Tela que acompanha os motoristas em tempo real."""

    template_name = 'mapas/ao_vivo.html'
    permissao_modulo = 'mapas'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        from apps.mapas.views.mapa import MapaPrincipalView

        ctx = super().get_context_data(**kwargs)
        filial = getattr(self.request, 'filial_ativa', None)
        ctx['title'] = 'Motoristas ao Vivo'
        ctx['centro'] = MapaPrincipalView._centro_inicial(filial)
        ctx['limite_online_s'] = LIMITE_ONLINE_S
        return ctx


@require_GET
@requer_permissao('mapas', 'ver')
def api_ao_vivo(request):
    """
    GET /mapas/api/ao-vivo/

    Posição atual de cada motorista, com velocidade, atraso e destino.
    Consultada em intervalo curto pela tela: por isso lê a tabela de última
    posição (uma linha por motorista) e não o histórico.
    """
    return JsonResponse({
        'motoristas': RastreioService.ao_vivo(
            getattr(request, 'filial_ativa', None)),
        'limite_online_s': LIMITE_ONLINE_S,
    })


@require_GET
@requer_permissao('mapas', 'ver')
def api_percurso(request, pk):
    """
    GET /mapas/api/percurso/<motorista_id>/?de=&ate=

    Trajeto percorrido, para desenhar a linha e mostrar km e velocidades.
    """
    return JsonResponse(RastreioService.percurso(
        getattr(request, 'filial_ativa', None), pk,
        inicio=_data(request.GET.get('de')),
        fim=_data(request.GET.get('ate')),
    ))


@require_POST
@requer_permissao('mapas', 'editar')
def api_limpar_rastreio(request, pk):
    """
    POST /mapas/api/rastreio/<motorista_id>/limpar/

    Descarta a posição e o percurso de um motorista. Exige permissão de
    **edição**, não de leitura: quem só acompanha o mapa não deveria conseguir
    apagar o histórico de ninguém.
    """
    from apps.cadastros.models import Motorista

    filial = getattr(request, 'filial_ativa', None)
    motorista = Motorista.objects.filter(
        pk=pk, filial__in=RastreioService._escopo(filial)).first()
    if motorista is None:
        return JsonResponse({'erro': 'Motorista não encontrado.'}, status=404)

    apagado = RastreioService.limpar_motorista(filial, motorista.pk)
    return JsonResponse({'ok': True, 'motorista': motorista.nome, **apagado})
