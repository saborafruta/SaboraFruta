"""
As telas da etiqueta: a folha para imprimir e os desenhos do QR e das barras.

A ETIQUETA É PÁGINA PRÓPRIA, e não um modal: o que vai para a impressora
térmica precisa de uma folha limpa, sem menu nem cabeçalho em volta. A
`@media print` esconde tudo que não é etiqueta — sem ela, sai uma folha A4
com o sistema inteiro em volta de um retângulo de 5 cm.
"""
import io

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.core.services.barras import suportado, svg as barras_svg
from apps.estoque.models import LoteProduto

from .services import ArmazenagemService, EtiquetaService
from .views import PolpaBaseView


def _filial(request):
    return request.filial_ativa


def _lote(request, pk) -> LoteProduto:
    return get_object_or_404(
        LoteProduto.objects.filter(filial=_filial(request))
        .select_related(
            'produto', 'produto__unidade_medida', 'produto__ficha_polpa',
            'armazenamento_polpa', 'armazenamento_polpa__camara',
        ),
        pk=pk,
    )


class EtiquetaListView(PolpaBaseView):
    """Os lotes que podem receber etiqueta."""

    area = 'frio'

    def get(self, request):
        filtros = {'busca': (request.GET.get('busca') or '').strip()}
        linhas = ArmazenagemService.estoque(_filial(request), filtros)

        return render(request, 'polpa/etiqueta_list.html', {
            'title': 'Etiquetas',
            'linhas': [
                {
                    **linha,
                    'pendencias': EtiquetaService.pendencias(linha['lote']),
                }
                for linha in linhas[:200]
            ],
            'filtros': filtros,
        })


class EtiquetaLoteView(PolpaBaseView):
    """A etiqueta de um lote, pronta para imprimir."""

    area = 'frio'

    def get(self, request, pk):
        lote = _lote(request, pk)
        try:
            copias = max(1, min(int(request.GET.get('copias') or 1), 60))
        except ValueError:
            copias = 1

        return render(request, 'polpa/etiqueta.html', {
            'title': f'Etiqueta — {lote.numero_lote}',
            'lote': lote,
            'dados': EtiquetaService.dados(lote),
            'pendencias': EtiquetaService.pendencias(lote),
            # `range` no template não existe; a lista de cópias vem pronta.
            'copias': range(copias),
            'quantidade_copias': copias,
            'url_qr': EtiquetaService.url_rastreabilidade(request, lote),
        })


class QrLoteView(PolpaBaseView):
    """
    O desenho do QR em PNG.

    Gerado sob demanda e não guardado: a imagem é derivada da URL, e um
    arquivo salvo seria mais uma coisa para sincronizar sem ganho nenhum.
    """

    area = 'frio'

    def get(self, request, pk):
        import qrcode

        lote = _lote(request, pk)
        imagem = qrcode.make(EtiquetaService.url_rastreabilidade(request, lote))
        buffer = io.BytesIO()
        imagem.save(buffer, format='PNG')
        return HttpResponse(buffer.getvalue(), content_type='image/png')


class BarrasLoteView(PolpaBaseView):
    """
    O mesmo lote em barras — para a pistola do almoxarifado.

    QR e barras convivem porque são aparelhos diferentes: o QR precisa de
    câmera (o celular), e o leitor de bancada é laser, varre uma linha e não
    enxerga QR nenhum.
    """

    area = 'frio'

    def get(self, request, pk):
        lote = _lote(request, pk)
        codigo = EtiquetaService.codigo_de_barras(lote)
        if not codigo or not suportado(codigo):
            # Melhor 404 do que um desenho que o leitor traduz para outro
            # código — erro silencioso é o pior tipo aqui.
            raise Http404('Lote sem código representável em barras.')
        return HttpResponse(barras_svg(codigo), content_type='image/svg+xml')
