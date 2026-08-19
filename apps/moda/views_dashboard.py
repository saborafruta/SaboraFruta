"""
Dashboard executivo do vertical Moda.

Uma view só, porque o painel é uma leitura só: não há ação, formulário nem
gravação aqui. Tudo que a tela mostra é derivado do que já está no banco.

O endereço é `/moda/indicadores/dashboard/`, que é para onde o menu já
apontava — a tela sai do placeholder sem que nenhum link mude.
"""
from django.shortcuts import render

from .services.dashboard import PERIODO_PADRAO, DashboardService
from .views import ModaBaseView


class DashboardView(ModaBaseView):
    """Os dezesseis indicadores e os oito gráficos."""

    def get(self, request):
        painel = DashboardService.painel(
            request.filial_ativa, dias=_dias(request),
        )
        return render(request, 'moda/dashboard.html', {
            'title': 'Dashboard',
            **painel,
            # Separados aqui e não no template: `{% if %}` dentro do laço
            # repetiria a regra em dois lugares, e a distinção entre número
            # de período e foto de agora é a coisa mais importante da tela.
            'do_periodo': [i for i in painel['indicadores'] if i.do_periodo],
            'de_agora': [i for i in painel['indicadores'] if not i.do_periodo],
        })


def _dias(request) -> int:
    """
    A janela pedida na querystring, ou a padrão.

    Valor inválido cai no padrão em silêncio: `?dias=abc` é link colado
    errado, não ataque, e uma tela de erro para isso seria desproporcional.
    O serviço ainda valida contra a lista de períodos aceitos.
    """
    try:
        return int(request.GET.get('dias', PERIODO_PADRAO))
    except (TypeError, ValueError):
        return PERIODO_PADRAO
