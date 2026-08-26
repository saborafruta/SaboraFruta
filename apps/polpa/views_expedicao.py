"""
As telas da expedição: a separação pela validade.

A TELA É DE QUEM ENTRA NA CÂMARA, e por isso ela mostra endereço e dias de
validade em vez de valores e impostos. Quem separa não precisa saber quanto
o pedido vale; precisa saber em qual rua está o lote e quantos dias ele
ainda tem.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError
from apps.vendas.models import PedidoVenda

from .services.separacao import SeparacaoPolpaService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _pedido(request, pk):
    return get_object_or_404(
        PedidoVenda.objects.filter(filial=_filial(request))
        .select_related('cliente'),
        pk=pk,
    )


class SeparacaoListView(PolpaBaseView):
    """Os pedidos esperando a câmara."""

    area = 'expedicao'

    def get(self, request):
        filtros = {'busca': (request.GET.get('busca') or '').strip()}
        linhas = SeparacaoPolpaService.pedidos(_filial(request), filtros)
        return render(request, 'polpa/separacao_list.html', {
            'title': 'Separação',
            'linhas': linhas,
            'filtros': filtros,
            'resumo': {
                'pedidos': len(linhas),
                'a_separar': sum(1 for l in linhas if not l['separacao']),
                'atrasados': sum(1 for l in linhas if l['atrasado']),
            },
        })


class SeparacaoPedidoView(PolpaBaseView):
    """A lista de separação de um pedido: lote, endereço e validade."""

    area = 'expedicao'

    def get(self, request, pk):
        pedido = _pedido(request, pk)
        return render(request, 'polpa/separacao_pedido.html', {
            'title': f'Separação — {pedido.numero_pedido}',
            'pedido': pedido,
            'linhas': SeparacaoPolpaService.mapa(pedido),
            'separacao': SeparacaoPolpaService.separacao_atual(pedido),
            'pode_agir': request.user.tem_permissao('polpa_expedicao', 'editar'),
        })

    def post(self, request, pk):
        pedido = _pedido(request, pk)
        volta = redirect(reverse('polpa:separacao-pedido', args=[pedido.pk]))

        if not request.user.tem_permissao('polpa_expedicao', 'editar'):
            messages.error(request, 'Sem permissão para fechar a separação.')
            return volta

        try:
            separacao = SeparacaoPolpaService.separar(pedido, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Separação {separacao.numero} fechada — os lotes estão presos '
            'ao pedido.',
        )
        return volta
