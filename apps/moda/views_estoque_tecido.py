"""
Estoque › Tecidos — malhas e tecidos em rolo.

Só leitura. O saldo é do estoque do ERP e se mexe por movimentação; o
consumo é gravado no corte; o vínculo com o produto de estoque é campo do
cadastro do tecido. Editar por aqui criaria um segundo lugar gravando cada
um deles.
"""
from django.shortcuts import render

from .services.estoque_tecido import PERIODOS, EstoqueTecidoService
from .views import ModaBaseView


class EstoqueTecidoView(ModaBaseView):
    """Saldo, consumo e cobertura de cada tecido."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp. Declarar
    # uma área que ninguém tem faria o hub oferecer a porta e a view dar
    # 403 do outro lado.
    area = 'pcp'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'
        busca = (request.GET.get('q') or '').strip()

        dados = EstoqueTecidoService.painel(request.filial_ativa, int(dias), busca)
        return render(request, 'moda/estoque_tecidos.html', {
            'title': 'Tecidos',
            'dias': dias,
            'periodos': PERIODOS,
            'busca': busca,
            **dados,
        })
