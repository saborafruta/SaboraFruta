"""
Indicador de Margens (grupo Indicadores).

A tela de Custos responde "quanto a ordem consumiu"; esta responde "o que
foi cobrado cobriu aquilo". O custo dos dois lados vem do mesmo serviço, de
propósito: números diferentes para a mesma ordem fariam as duas telas
perderem a confiança de uma vez.
"""
from decimal import Decimal

from django.shortcuts import render

from .services.margem import PERIODOS, MargemService
from .views import ModaBaseView

VISOES = (
    ('pedido', 'Por pedido'),
    ('produto', 'Por produto'),
    ('ordem', 'Por ordem'),
)
ROTULOS = {'pedido': 'Pedido', 'produto': 'Produto', 'ordem': 'Ordem'}


def _do_pedido(g) -> dict:
    return {
        **g,
        'titulo': f'Pedido {g["numero"]}',
        'subtitulo': f'{g["cliente"]} · {g["ordens"]} ordem(ns)',
    }


def _do_produto(g) -> dict:
    partes = [p for p in (g['codigo'], f'{g["ordens"]} ordem(ns)') if p]
    return {**g, 'titulo': g['nome'], 'subtitulo': ' · '.join(partes)}


def _da_ordem(l) -> dict:
    """
    A linha da ordem usa outros nomes de campo que os agrupamentos.

    Normalizada aqui e não no serviço: o serviço responde pela conta, e
    renomear campo para caber num template é trabalho de apresentação.
    """
    return {
        **l,
        'titulo': l['numero'],
        'subtitulo': f'{l["produto"]} · {l["cliente"]}',
        'custo': l['real'],
        'pecas': l['boas'],
        'por_peca': (
            (l['margem'] / l['boas']).quantize(Decimal('0.01'))
            if l['boas'] and not l['sem_preco'] else None
        ),
    }


class MargemIndicadorView(ModaBaseView):
    """Receita contra custo real das ordens concluídas no período."""

    area = 'indicadores'

    def get(self, request):
        dias = (request.GET.get('dias') or '30').strip()
        if dias not in dict(PERIODOS):
            dias = '30'
        visao = (request.GET.get('visao') or 'pedido').strip()
        if visao not in ROTULOS:
            visao = 'pedido'

        dados = MargemService.painel(request.filial_ativa, int(dias))
        if visao == 'produto':
            itens = [_do_produto(g) for g in dados['por_produto']]
        elif visao == 'ordem':
            itens = [_da_ordem(l) for l in dados['linhas']]
        else:
            itens = [_do_pedido(g) for g in dados['por_pedido']]

        return render(request, 'moda/margem_indicador.html', {
            'title': 'Margens',
            'dias': dias,
            'visao': visao,
            'visoes': VISOES,
            'rotulo_coluna': ROTULOS[visao],
            'itens': itens,
            'periodos': PERIODOS,
            **dados,
        })
