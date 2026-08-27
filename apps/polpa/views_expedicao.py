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
from apps.polpa.models import EntregaFria
from apps.vendas.models import PedidoVenda

from .services.carregamento import CarregamentoService
from .services.entrega import EntregaService
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
        filial = _filial(request)
        filtros = {
            'busca': (request.GET.get('busca') or '').strip(),
            'busca_venda': (request.GET.get('busca_venda') or '').strip(),
        }
        linhas = CarregamentoService.cargas(filial, filtros)
        vendas = CarregamentoService.vendas_para_carregar(filial, filtros)
        return render(request, 'polpa/carregamento_list.html', {
            'title': 'Carregamento',
            'linhas': linhas,
            'vendas': vendas,
            'filtros': filtros,
            'resumo': {
                'na_doca': sum(1 for l in linhas if l['na_doca']),
                'sem_medicao': sum(
                    1 for l in linhas if l['na_doca'] and l['sem_medicao']
                ),
                'sairam': sum(1 for l in linhas if not l['na_doca']),
                'a_carregar': len(vendas),
            },
            'pode_agir': request.user.tem_permissao('polpa_expedicao', 'editar'),
        })

    def post(self, request):
        """
        Monta a carga com as vendas marcadas e abre a conferencia dela.

        VAI DIRETO PARA A CONFERENCIA, e nao volta para a lista: quem acabou de
        escolher os pedidos esta' com o caminhao encostado, e o proximo passo e'
        conferir a carga e medir o bau -- nao olhar a doca de novo.
        """
        from apps.vendas.models.pedido import PedidoVenda

        filial = _filial(request)
        volta = redirect(reverse('polpa:carregamento'))

        if not request.user.tem_permissao('polpa_expedicao', 'editar'):
            messages.error(request, 'Você pode ver a doca, mas não montar carga.')
            return volta

        # OS PEDIDOS SAO BUSCADOS PELA FILIAL, e nao so' pelos ids do
        # formulario: id colado a mao montaria carga com pedido de outra
        # unidade, e o caminhao sairia com mercadoria que nao e' desta casa.
        ids = [i for i in request.POST.getlist('pedidos') if i.isdigit()]
        pedidos = list(
            PedidoVenda.objects.filter(filial=filial, pk__in=ids)
            .select_related('cliente')
        )

        try:
            romaneio = CarregamentoService.montar_carga(
                filial, pedidos, request.POST, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
            return volta

        messages.success(
            request,
            f'Carga {romaneio.numero} montada com {len(pedidos)} pedido(s).',
        )
        return redirect(reverse('polpa:carregamento-carga', args=[romaneio.pk]))


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


class EntregasView(PolpaBaseView):
    """As entregas da rua: quem recebeu, quando e em que temperatura."""

    area = 'expedicao'

    def get(self, request):
        filtros = {'busca': (request.GET.get('busca') or '').strip()}
        linhas = EntregaService.paradas(_filial(request), filtros)
        return render(request, 'polpa/entregas.html', {
            'title': 'Entregas',
            'linhas': linhas,
            'filtros': filtros,
            'resumo': EntregaService.resumo(linhas),
            'ocorrencias': EntregaFria.Ocorrencia.choices,
            'pode_agir': request.user.tem_permissao('polpa_expedicao', 'editar'),
        })

    def post(self, request):
        volta = redirect(reverse('polpa:entregas'))
        if not request.user.tem_permissao('polpa_expedicao', 'editar'):
            messages.error(request, 'Sem permissão para registrar entrega.')
            return volta

        parada = get_object_or_404(
            ItemRomaneioCarga.objects.filter(romaneio__filial=_filial(request))
            .select_related('romaneio'),
            pk=request.POST.get('parada'),
        )
        temperatura = _numero(request.POST.get('temperatura'))

        try:
            if request.POST.get('acao') == 'entregar':
                EntregaService.entregar(parada, {
                    'recebido_por': request.POST.get('recebido_por'),
                    'documento': request.POST.get('documento'),
                    'temperatura': temperatura,
                    'observacao': request.POST.get('observacao'),
                }, request.user)
                messages.success(
                    request, f'{parada.cliente_nome}: entrega registrada.',
                )
                _avisar_entrega(request, parada, temperatura)
            elif request.POST.get('acao') == 'nao-entregar':
                EntregaService.nao_entregar(parada, {
                    'ocorrencia': request.POST.get('ocorrencia'),
                    'observacao': request.POST.get('observacao'),
                    'temperatura': temperatura,
                }, request.user)
                messages.warning(
                    request,
                    f'{parada.cliente_nome}: não entregue — a ocorrência ficou '
                    'registrada.',
                )
            else:
                messages.error(request, 'Ação desconhecida.')
        except DomainError as erro:
            messages.error(request, str(erro))

        return volta


def _avisar_entrega(request, parada, temperatura):
    """
    Chegou acima do que o produto exige? A tela diz na hora.

    O motorista ainda está na porta do cliente quando isso aparece — e é o
    único momento em que alguém pode fazer alguma coisa a respeito.
    """
    exigida = EntregaService.temperatura_exigida(parada)
    if temperatura is None:
        messages.warning(
            request,
            'Entrega sem temperatura medida — a cadeia de frio fica sem a '
            'última prova.',
        )
        return
    if exigida is not None and temperatura > exigida:
        messages.warning(
            request,
            f'Chegou a {temperatura}°C — o produto exige {exigida}°C ou menos.',
        )
