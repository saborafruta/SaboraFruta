"""
Indicador de Prazos (grupo Indicadores).

Duas perguntas na mesma tela, e só uma delas dá para consertar: o placar do
que já foi entregue é histórico, e a lista do que está aberto é a de hoje.
"""
from django.shortcuts import render

from .services.prazo import PERIODOS, PrazoService
from .views import ModaBaseView


class PrazoIndicadorView(ModaBaseView):
    """Entregas no prazo, e o risco do que ainda não saiu."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'

        dados = PrazoService.painel(request.filial_ativa, int(dias))
        return render(request, 'moda/prazo_indicador.html', {
            'title': 'Prazos',
            'dias': dias,
            'periodos': PERIODOS,
            **dados,
        })
