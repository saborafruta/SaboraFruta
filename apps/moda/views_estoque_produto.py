"""
Estoque › Produtos — itens de catálogo em estoque.

Não confundir com `Produtos › Catálogo`, que é o cadastro: aquela responde
"quais produtos existem e como são"; esta responde "quanto há de cada um,
quanto está vindo e há quanto tempo nada sai".

Só leitura. O saldo se mexe por movimentação de estoque, e o vínculo com o
produto do ERP é campo do cadastro do produto.
"""
from django.shortcuts import render

from .services.estoque_produto import FILTROS, EstoqueProdutoService
from .views import ModaBaseView


class EstoqueProdutoView(ModaBaseView):
    """Saldo, produção em curso e tempo parado de cada produto."""

    # `pcp` e não `estoque`: não existe área de estoque no vertical, e o
    # `AREA_POR_GRUPO` já manda o grupo inteiro do menu para pcp.
    area = 'pcp'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        filtro = (request.GET.get('filtro') or '').strip()
        if filtro not in FILTROS:
            filtro = ''

        dados = EstoqueProdutoService.painel(request.filial_ativa, busca, filtro)
        return render(request, 'moda/estoque_produtos.html', {
            'title': 'Produtos',
            'busca': busca,
            'filtro': filtro,
            **dados,
        })
