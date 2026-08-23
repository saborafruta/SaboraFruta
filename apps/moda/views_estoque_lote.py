"""
Estoque › Lotes — rastreabilidade por lote.

Só leitura. O lote é digitado no registro de corte, que é onde ele de fato
acontece: alguém pega o rolo, lê a etiqueta e anota. Um cadastro próprio de
lote aqui criaria uma segunda verdade sobre o mesmo rolo.
"""
from django.shortcuts import render

from .services.estoque_lote import EstoqueLoteService
from .views import ModaBaseView


class EstoqueLoteView(ModaBaseView):
    """De que rolo saiu cada peça, e para quem ela foi."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp.
    area = 'pcp'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        dados = EstoqueLoteService.painel(request.filial_ativa, busca)
        return render(request, 'moda/estoque_lotes.html', {
            'title': 'Lotes',
            'busca': busca,
            **dados,
        })
