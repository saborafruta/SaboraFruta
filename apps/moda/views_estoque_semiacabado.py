"""
Estoque › Semiacabados — peças em processo (WIP).

Não confundir com `Indicadores › WIP`: aquele responde "onde o trabalho está
e o que travou"; este responde "quanto vale o que está lá e há quanto tempo
está parado". Mesma matéria-prima, perguntas diferentes.

Só leitura. Quem move a peça de um balde para outro é o apontamento do
fluxo, na tela da ordem.
"""
from django.shortcuts import render

from .services.estoque_semiacabado import EstoqueSemiacabadoService
from .views import ModaBaseView


class EstoqueSemiacabadoView(ModaBaseView):
    """Valor e idade do que está no chão de fábrica."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp.
    area = 'pcp'

    def get(self, request):
        dados = EstoqueSemiacabadoService.painel(request.filial_ativa)
        return render(request, 'moda/estoque_semiacabados.html', {
            'title': 'Semiacabados',
            **dados,
        })
