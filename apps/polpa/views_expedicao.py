"""
As telas da expedição: a separação pela validade.

A TELA É DE QUEM ENTRA NA CÂMARA, e por isso ela mostra endereço e dias de
validade em vez de valores e impostos. Quem separa não precisa saber quanto
o pedido vale; precisa saber em qual rua está o lote e quantos dias ele
ainda tem.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.logistica.models import ItemRomaneioCarga, RomaneioCarga
from apps.vendas.models import PedidoVenda

from .services.carregamento import CarregamentoService
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


class CarregamentoListView(PolpaBaseView):
    """As cargas na doca e as que saíram hoje."""

    area = 'expedicao'

    def get(self, request):
        filtros = {'busca': (request.GET.get('busca') or '').strip()}
        linhas = CarregamentoService.cargas(_filial(request), filtros)
        return render(request, 'polpa/carregamento_list.html', {
            'title': 'Carregamento',
            'linhas': linhas,
            'filtros': filtros,
            'resumo': {
                'na_doca': sum(1 for l in linhas if l['na_doca']),
                'sem_medicao': sum(
                    1 for l in linhas if l['na_doca'] and l['sem_medicao']
                ),
                'sairam': sum(1 for l in linhas if not l['na_doca']),
            },
        })


class CarregamentoView(PolpaBaseView):
    """A conferência de uma carga e a medição do baú."""

    area = 'expedicao'

    def get(self, request, pk):
        romaneio = _romaneio(request, pk)
        return render(request, 'polpa/carregamento.html', {
            'title': f'Carregamento — {romaneio}',
            **CarregamentoService.carga(romaneio),
            'pode_agir': request.user.tem_permissao('polpa_expedicao', 'editar'),
        })

    def post(self, request, pk):
        romaneio = _romaneio(request, pk)
        volta = redirect(reverse('polpa:carregamento-carga', args=[romaneio.pk]))

        if not request.user.tem_permissao('polpa_expedicao', 'editar'):
            messages.error(request, 'Sem permissão para mexer na carga.')
            return volta

        acao = request.POST.get('acao')
        try:
            if acao == 'conferir':
                item = get_object_or_404(
                    ItemRomaneioCarga.objects.filter(romaneio=romaneio),
                    pk=request.POST.get('item'),
                )
                CarregamentoService.conferir(
                    item, request.POST.get('carregada') == '1',
                )
            elif acao == 'medir':
                ficha = CarregamentoService.registrar_temperatura(
                    romaneio, _numero(request.POST.get('temperatura')),
                    request.user,
                )
                _avisar_temperatura(request, romaneio, ficha)
            elif acao == 'despachar':
                ficha = CarregamentoService.despachar(
                    romaneio, _numero(request.POST.get('temperatura')),
                    request.user,
                )
                messages.success(
                    request,
                    f'{romaneio} saiu às '
                    f'{timezone.localtime(ficha.saida_em):%H:%M} com o baú a '
                    f'{ficha.temperatura_bau}°C.',
                )
                _avisar_temperatura(request, romaneio, ficha)
            else:
                messages.error(request, 'Ação desconhecida.')
        except DomainError as erro:
            messages.error(request, str(erro))

        return volta


def _romaneio(request, pk):
    return get_object_or_404(
        RomaneioCarga.objects.filter(filial=_filial(request))
        .select_related('transportadora'),
        pk=pk,
    )


def _avisar_temperatura(request, romaneio, ficha):
    """
    O desvio do baú volta na tela na hora.

    Descobrir no relatório do mês que um caminhão saiu a -8°C é descobrir
    quando o produto já foi entregue, comido ou devolvido.
    """
    exigida = CarregamentoService.temperatura_exigida(romaneio)
    if exigida is None or ficha.temperatura_bau is None:
        return
    if ficha.temperatura_bau > exigida:
        messages.warning(
            request,
            f'Baú a {ficha.temperatura_bau}°C — a carga exige {exigida}°C ou '
            'menos.',
        )


def _numero(valor):
    """Decimal do formulário — vazio é `None`, não zero."""
    texto = (valor or '').strip().replace(',', '.')
    if not texto:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None
