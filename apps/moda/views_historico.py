"""
Linha do tempo do pedido e da ordem de produção.

Leitura pura, e sem ação nenhuma: auditoria que se pode editar não é
auditoria. Nem apagar, nem corrigir, nem ocultar — o registro é o que
sobrou do que aconteceu.
"""
from django.shortcuts import get_object_or_404, render

from .models import OrdemProducao, PedidoProducao
from .services.historico import HistoricoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


class HistoricoPedidoView(ModaBaseView):
    """Tudo que já aconteceu com um pedido, do lançamento à entrega."""

    area = 'comercial'

    def get(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request)).select_related('cliente'),
            pk=pk,
        )
        eventos = HistoricoService.do_pedido(pedido)
        return render(request, 'moda/historico.html', {
            'title': f'Histórico do pedido #{pedido.numero:06d}',
            'raiz': pedido,
            'subtitulo': str(pedido.cliente),
            'voltar': ('moda:pedido-detail', pedido.pk),
            'eventos': eventos,
            'marcos': [e for e in eventos if e.marco],
        })


class HistoricoOrdemView(ModaBaseView):
    """
    O mesmo, do ponto de vista da ordem.

    Existe separado porque a fábrica pergunta pela OP, não pelo pedido: o
    encarregado tem o número da ordem na ficha em mãos, e chegar pelo pedido
    exigiria descobrir de qual pedido aquela OP saiu.
    """

    area = 'pcp'

    def get(self, request, pk):
        ordem = get_object_or_404(
            OrdemProducao.objects.for_filial(_filial(request)).select_related(
                'pedido', 'pedido__cliente',
            ),
            pk=pk,
        )
        eventos = HistoricoService.da_ordem(ordem)
        return render(request, 'moda/historico.html', {
            'title': f'Histórico da {ordem.numero}',
            'raiz': ordem,
            'subtitulo': f'Pedido #{ordem.pedido.numero:06d} · {ordem.cliente}',
            'voltar': ('moda:ordem-detail', ordem.pk),
            'eventos': eventos,
            'marcos': [e for e in eventos if e.marco],
        })
