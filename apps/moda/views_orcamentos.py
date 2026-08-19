"""
Orçamentos: a lista das propostas em aberto e o botão que fecha.

Fechar exige `aprovar`, não `editar` — é o mesmo critério da liberação
interna do pedido: assumir preço e prazo com o cliente não é a mesma coisa
que digitar o orçamento.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import PedidoProducao
from .services.orcamentos import DIAS_PARA_ESFRIAR, OrcamentoService
from .views import ModaBaseView


class OrcamentoListView(ModaBaseView):
    """As propostas que ainda não viraram pedido."""

    area = 'comercial'

    def get(self, request):
        busca = (request.GET.get('q') or '').strip()
        linhas = OrcamentoService.listar(request.filial_ativa, busca=busca)

        return render(request, 'moda/orcamentos.html', {
            'title': 'Orçamentos',
            'linhas': linhas,
            'resumo': OrcamentoService.resumo(linhas),
            'busca': busca,
            'dias_esfriar': DIAS_PARA_ESFRIAR,
            'pode_fechar': request.user.tem_permissao('moda_comercial', 'aprovar'),
        })


class OrcamentoFecharView(ModaBaseView):
    """
    Fecha a proposta: o orçamento vira pedido.

    Uma troca de status, não uma cópia de registro — o número, a arte, a
    grade e as pessoas lançadas continuam onde estavam.
    """

    area = 'comercial'
    permissao_acao = 'aprovar'

    def post(self, request, pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(request.filial_ativa), pk=pk,
        )
        try:
            OrcamentoService.fechar(pedido, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:orcamentos'))

        messages.success(
            request,
            f'Orçamento #{pedido.numero:06d} fechado — agora é pedido. '
            f'O próximo passo é a aprovação do cliente.',
        )
        # Leva para o pedido, e não de volta à lista: quem fecha quer
        # continuar dali (mandar para o cliente, emitir a OP), e voltar para
        # uma lista onde ele já não aparece parece que algo se perdeu.
        return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))
