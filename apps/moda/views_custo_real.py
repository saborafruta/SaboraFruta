"""
Indicador de Custos (grupo Indicadores) — real contra a ficha técnica.

Não confundir com `views_custos.py`, que é a tela de Engenharia: aquela
responde "quanto ESTE produto deveria custar", lendo ficha e roteiro; esta
responde "quanto as ordens do mês custaram de verdade". Uma é projeto, a
outra é resultado, e é justamente a diferença entre as duas que interessa.
"""
from django.shortcuts import render

from .services.custo_real import PERIODOS, CustoRealService
from .views import ModaBaseView


class CustoIndicadorView(ModaBaseView):
    """Previsto × real das ordens concluídas no período."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'

        dados = CustoRealService.painel(request.filial_ativa, int(dias))
        return render(request, 'moda/custo_indicador.html', {
            'title': 'Custos',
            'dias': dias,
            'periodos': PERIODOS,
            **dados,
        })
