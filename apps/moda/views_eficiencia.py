"""
Indicador de Eficiência (grupo Indicadores).

Só leitura. O que alimenta estes números é cadastro (roteiro e capacidade)
e apontamento de etapa, cada um na tela dele — deixar editar por aqui
criaria um segundo lugar gravando o mesmo campo.
"""
from django.shortcuts import render

from .services.eficiencia import PERIODOS, EficienciaService
from .views import ModaBaseView


class EficienciaIndicadorView(ModaBaseView):
    """Minutos ganhos contra minutos disponíveis, setor a setor."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'

        dados = EficienciaService.painel(request.filial_ativa, int(dias))
        return render(request, 'moda/eficiencia_indicador.html', {
            'title': 'Eficiência',
            'dias': dias,
            'periodos': PERIODOS,
            **dados,
        })
