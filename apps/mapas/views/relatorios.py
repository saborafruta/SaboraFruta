"""Relatórios imprimíveis do módulo de mapas."""
from __future__ import annotations

import datetime

from django.views.generic import TemplateView
from django.utils import timezone

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.mapas.services.relatorios import (
    AGRUPAMENTOS,
    RelatorioCoberturaService,
    RelatorioRegiaoService,
    RelatorioRotasService,
)
from apps.mapas.services.roteiro import RelatorioCompletoService


def _data(valor):
    try:
        return datetime.date.fromisoformat(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


class BaseRelatorioMapas(PermissaoRequiredMixin, TemplateView):
    """
    Base dos relatórios do módulo.

    A permissão é a de **relatórios**, não a de mapas: quem tira relatório
    para uma reunião não é necessariamente quem opera o mapa, e o hub de
    Relatórios já é governado por essa permissão — exigir as duas esconderia
    o item de quem tem acesso ao hub.
    """

    permissao_modulo = 'relatorios'
    permissao_acao = 'ver'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['filial'] = getattr(self.request, 'filial_ativa', None)
        ctx['gerado_em'] = timezone.localtime()
        return ctx


class RelatorioRegiaoView(BaseRelatorioMapas):
    """Vendas por cidade, bairro, zona ou estado."""

    template_name = 'mapas/relatorio_regiao.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        agrupar = self.request.GET.get('agrupar', 'cidade')

        ctx['title'] = 'Vendas por Região'
        ctx['agrupamentos'] = [
            {'chave': k, 'rotulo': v} for k, v in AGRUPAMENTOS.items()
        ]
        # Só controla o que vai impresso: na tela o detalhe abre no clique.
        ctx['detalhar'] = self.request.GET.get('detalhar') == '1'
        ctx['dados'] = RelatorioRegiaoService.gerar(
            ctx['filial'],
            agrupar_por=agrupar,
            inicio=_data(self.request.GET.get('de')),
            fim=_data(self.request.GET.get('ate')),
            cidade=self.request.GET.get('cidade', '').strip(),
            uf=self.request.GET.get('uf', '').strip(),
            representante_id=_inteiro(self.request.GET.get('representante')),
            filial_id=_inteiro(self.request.GET.get('filial')),
            regiao=self.request.GET.get('regiao', '').strip(),
        )
        return ctx


class RelatorioCoberturaView(BaseRelatorioMapas):
    """Clientes fora do mapa por falta de coordenada."""

    template_name = 'mapas/relatorio_cobertura.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Cobertura de Geolocalização'
        ctx['dados'] = RelatorioCoberturaService.gerar(
            ctx['filial'],
            cidade=self.request.GET.get('cidade', '').strip(),
            uf=self.request.GET.get('uf', '').strip(),
        )
        return ctx


class RelatorioRotasView(BaseRelatorioMapas):
    """Rotas calculadas no período e economia da otimização."""

    template_name = 'mapas/relatorio_rotas.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Rotas e Otimização'
        ctx['dados'] = RelatorioRotasService.gerar(
            ctx['filial'],
            inicio=_data(self.request.GET.get('de')),
            fim=_data(self.request.GET.get('ate')),
        )
        return ctx


class RelatorioCompletoView(BaseRelatorioMapas):
    """
    Documento único: faturamento por zona e por bairro, clientes de cada zona,
    quem está sem endereço e o roteiro sugerido.
    """

    template_name = 'mapas/relatorio_completo.html'

    def get_context_data(self, **kwargs):
        from apps.mapas.services.heatmap import ZONAS

        ctx = super().get_context_data(**kwargs)
        ctx['title'] = 'Relatório de Mapas'
        ctx['zonas'] = [{'chave': k, 'rotulo': v} for k, v in ZONAS.items()]
        ctx['dados'] = RelatorioCompletoService.gerar(
            ctx['filial'],
            inicio=_data(self.request.GET.get('de')),
            fim=_data(self.request.GET.get('ate')),
            cidade=self.request.GET.get('cidade', '').strip(),
            uf=self.request.GET.get('uf', '').strip(),
            zona=self.request.GET.get('zona', '').strip(),
        )
        return ctx
