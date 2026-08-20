"""
Aprovação interna, aprovação do cliente e o mapa dos 23 passos.

A aprovação do cliente NÃO fica aqui: ela acontece na página pública, sem
login, em `views_publico`. Aqui é o lado de dentro — liberar o pedido e
acompanhar o que o cliente respondeu.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import AprovacaoPedido, PedidoProducao
from .services.aprovacao import FilaAprovacaoService
from .services.arte import FilaArteService
from .services.envio import EnvioProducaoService
from .services.fluxo_completo import FluxoCompletoService
from .services.validacao import ValidacaoProducao
from .views import ModaBaseView


def _pedido(request, pk) -> PedidoProducao:
    return get_object_or_404(
        PedidoProducao.objects.for_filial(request.filial_ativa)
        .select_related('cliente', 'aprovacao'),
        pk=pk,
    )


def aprovacao_de(pedido) -> AprovacaoPedido:
    """
    A aprovação do pedido, criada na primeira vez que alguém pergunta.

    Criar junto com o pedido encheria a tabela de linhas vazias para
    orçamento que nunca virou nada.
    """
    aprovacao, _ = AprovacaoPedido.objects.get_or_create(pedido=pedido)
    return aprovacao


class FluxoPedidoView(ModaBaseView):
    """Onde o pedido está nos 23 passos, e qual é o próximo."""

    area = 'comercial'

    def get(self, request, pk):
        pedido = _pedido(request, pk)
        return render(request, 'moda/fluxo_pedido.html', {
            'title': f'Fluxo do pedido #{pedido.numero:06d}',
            'pedido': pedido,
            **FluxoCompletoService.do_pedido(pedido),
            # As onze validações ao lado dos 23 passos: o painel diz onde o
            # pedido está, e a lista diz o que impede ele de descer para a
            # fábrica. Descobrir isso só ao clicar em emitir seria descobrir
            # tarde.
            'validacao': ValidacaoProducao.resumo(pedido),
        })


class AprovacaoPedidoView(ModaBaseView):
    """Liberação interna do pedido e a resposta do cliente."""

    area = 'comercial'

    def get(self, request, pk):
        pedido = _pedido(request, pk)
        return render(request, 'moda/aprovacao.html', {
            'title': f'Aprovação do pedido #{pedido.numero:06d}',
            'pedido': pedido,
            'aprovacao': aprovacao_de(pedido),
            'link_publico': request.build_absolute_uri(
                reverse('moda_publico:pedido', args=[pedido.token_publico])
            ),
        })


class LiberarPedidoView(ModaBaseView):
    """
    A aprovação interna: comercial e financeiro conferem antes de enviar.

    Exige `aprovar`, e não `editar`: liberar um pedido para o cliente é
    assumir preço e prazo, e o sistema de permissões já separa quem executa
    de quem aprova.
    """

    area = 'comercial'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        pedido = _pedido(request, pk)
        aprovacao = aprovacao_de(pedido)

        if aprovacao.liberado:
            messages.info(request, 'Este pedido já estava liberado.')
        else:
            aprovacao.liberar(request.user, request.POST.get('observacao', ''))
            messages.success(
                request,
                'Pedido liberado. Agora é só mandar o link ou o PDF para o cliente.',
            )
        return redirect(self._destino(request, pedido))

    @staticmethod
    def _destino(request, pedido) -> str:
        """
        Volta para onde a pessoa estava.

        Quem libera pela FILA quer liberar os próximos; mandá-la para a tela
        de um pedido só a obrigaria a voltar a cada liberação. Só aceita
        endereço de dentro do vertical: um `proximo` livre viraria redirect
        aberto -- alguém manda um link que passa pelo sistema e sai num site
        qualquer, com a cara de que foi o ERP que levou lá.
        """
        proximo = request.POST.get('proximo') or ''
        if proximo.startswith('/moda/') and '//' not in proximo[1:]:
            return proximo
        return reverse('moda:pedido-aprovacao', args=[pedido.pk])


class FilaAprovacaoView(ModaBaseView):
    """
    A fila do comercial: o que está esperando a casa e o que espera o cliente.

    Endereço do menu (`comercial/aprovacao-pedido/`), que até agora devolvia
    a tela de "em construção".
    """

    area = 'comercial'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        dados = FilaAprovacaoService.montar(request.filial_ativa, busca=busca)

        return render(request, 'moda/aprovacao_fila.html', {
            'title': 'Aprovação de pedido',
            'busca': busca,
            # Liberar é permissão de APROVAR, não de editar: assumir preço e
            # prazo perante o cliente é decisão de quem aprova. Sem ela, a
            # fila continua visível -- ver a espera é do comercial inteiro.
            'pode_liberar': request.user.tem_permissao('moda', 'aprovar'),
            **dados,
        })


class FilaArteView(ModaBaseView):
    """
    A fila da arte: o que falta desenhar, o que espera aceite do layout.

    Endereço do menu (`comercial/aprovacao-arte/`), que até agora devolvia a
    tela de "em construção".
    """

    area = 'comercial'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        dados = FilaArteService.montar(request.filial_ativa, busca=busca)

        return render(request, 'moda/arte_fila.html', {
            'title': 'Aprovação de arte',
            'busca': busca,
            'pode_liberar': request.user.tem_permissao('moda', 'aprovar'),
            **dados,
        })


class EnvioProducaoView(ModaBaseView):
    """
    A passagem para a fábrica, vista da carteira inteira.

    Endereço do menu (`comercial/envio-pedido/`), que até agora devolvia a
    tela de "em construção". O botão de emitir continua sendo o mesmo
    serviço da tela do pedido — aqui ele ganha a fila e o motivo da trava.
    """

    area = 'comercial'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        dados = EnvioProducaoService.montar(request.filial_ativa, busca=busca)

        return render(request, 'moda/envio_fila.html', {
            'title': 'Envio de pedido',
            'busca': busca,
            # Emitir OP é `criar`, a mesma permissão da tela do pedido: quem
            # não pode lá não pode aqui.
            'pode_emitir': request.user.tem_permissao('moda', 'criar'),
            **dados,
        })
