"""
Estoque › Aviamentos — insumos de montagem.

Não confundir com `views_insumos.py`, que é a tela de Engenharia: aquela
responde "quais fichas usam este zíper e o preço está igual em todas";
esta responde "quanto tem, e as ordens abertas cabem nisso". Catálogo de um
lado, posição de estoque do outro.

Só leitura. O aviamento continua sendo cadastrado onde ele existe — dentro
da ficha da peça — e o saldo se mexe por movimentação de estoque.
"""
from django.shortcuts import render

from .services.estoque_aviamento import TIPOS_AVIAMENTO, EstoqueAviamentoService
from .views import ModaBaseView

FILTROS = ('faltando', 'sem_vinculo', 'preco_divergente')


class EstoqueAviamentoView(ModaBaseView):
    """Saldo de cada aviamento contra a demanda das ordens abertas."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp.
    area = 'pcp'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        filtro = (request.GET.get('filtro') or '').strip()
        if filtro not in FILTROS:
            filtro = ''

        dados = EstoqueAviamentoService.painel(request.filial_ativa, busca, filtro)
        return render(request, 'moda/estoque_aviamentos.html', {
            'title': 'Aviamentos',
            'busca': busca,
            'filtro': filtro,
            'tipos': [(t.value, t.label) for t in TIPOS_AVIAMENTO],
            **dados,
        })
