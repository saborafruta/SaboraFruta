"""
Indicador de Perdas (grupo Indicadores).

Só leitura. Refugo vem do apontamento das etapas, retrabalho e causa vêm
da inspeção de qualidade, e o tecido vem do registro de corte — cada um
editável na tela dele.
"""
from django.shortcuts import render

from .services.perdas import PERIODOS, PerdasService
from .views import ModaBaseView


class PerdasIndicadorView(ModaBaseView):
    """Refugo, retrabalho e sobra de tecido — três perdas, duas unidades."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'

        dados = PerdasService.painel(request.filial_ativa, int(dias))
        return render(request, 'moda/perdas_indicador.html', {
            'title': 'Perdas',
            'dias': dias,
            'periodos': PERIODOS,
            **dados,
        })
