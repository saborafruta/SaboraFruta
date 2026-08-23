"""
Estoque › Acabados — prontas para expedir.

Não confundir com as filas de `Expedição`: aquelas são de quem trabalha o
documento (conferir, embalar, despachar); esta é de estoque — quanto há de
peça pronta, quanto vale e o que já passou do prazo do cliente.

Só leitura. Quem move a caixa de uma etapa para outra é a tela da expedição.
"""
from django.shortcuts import render

from .services.estoque_acabado import EstoqueAcabadoService
from .views import ModaBaseView


class EstoqueAcabadoView(ModaBaseView):
    """Peça pronta que ainda não saiu, contra o prazo do pedido."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp.
    area = 'pcp'

    def get(self, request):
        dados = EstoqueAcabadoService.painel(request.filial_ativa)
        return render(request, 'moda/estoque_acabados.html', {
            'title': 'Acabados',
            **dados,
        })
