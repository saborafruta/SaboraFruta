"""Painel de indicadores do módulo de mapas (§14)."""
from __future__ import annotations

from django.views.generic import TemplateView

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.mapas.services.painel import PainelService


class PainelMapasView(PermissaoRequiredMixin, TemplateView):
    template_name = 'mapas/painel.html'
    permissao_modulo = 'mapas'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filial = getattr(self.request, 'filial_ativa', None)
        inicio, fim = PainelService.periodo_de(self.request)

        ctx['title'] = 'Painel de Mapas'
        ctx['inicio'] = inicio
        ctx['fim'] = fim
        ctx['ind'] = PainelService.indicadores(filial, inicio=inicio, fim=fim)
        return ctx
