"""
Telas do fluxo de produção.

Duas: o painel do chão de fábrica (`producao/painel-fluxo`), que é o que o
encarregado abre de manhã, e o fluxo de uma ordem, que fica dentro da OP.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import EtapaOrdem, OrdemProducao
from .services import FluxoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _ordem(request, pk) -> OrdemProducao:
    return get_object_or_404(
        OrdemProducao.objects.for_filial(_filial(request))
        .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
        .prefetch_related('etapas'),
        pk=pk,
    )


class PainelFluxoView(ModaBaseView):
    """Todas as ordens abertas e a etapa em que cada uma está."""

    def get(self, request):
        linhas = FluxoService.painel(_filial(request))
        return render(request, 'moda/fluxo_painel.html', {
            'title': 'Fluxo de Produção',
            'linhas': linhas,
            'etapas_do_fluxo': EtapaOrdem.Etapa.choices,
            'total_atrasadas': sum(1 for l in linhas if l['atrasadas']),
        })


class FluxoOrdemView(ModaBaseView):
    """O fluxo de uma ordem, etapa por etapa."""

    def get(self, request, pk):
        ordem = _ordem(request, pk)
        return render(request, 'moda/fluxo_ordem.html', self.contexto(request, ordem))

    @staticmethod
    def contexto(request, ordem) -> dict:
        editaveis = FluxoService.campos_editaveis(request.user)
        return {
            'title': f'Fluxo — {ordem.numero}',
            'ordem': ordem,
            'editaveis': editaveis,
            'pode_apontar': bool(editaveis) and not ordem.encerrada,
            'status_choices': EtapaOrdem.Status.choices,
            **FluxoService.resumo(ordem),
        }


class ApontarView(ModaBaseView):
    """
    Grava o apontamento de uma etapa.

    A permissão real é conferida no serviço, campo por campo — por isso aqui
    basta 'ver': quem só vê não terá nenhum campo autorizado e o serviço não
    grava nada.
    """

    permissao_acao = 'ver'

    def post(self, request, pk, etapa_pk):
        ordem = _ordem(request, pk)
        etapa = get_object_or_404(EtapaOrdem, pk=etapa_pk, ordem=ordem)

        try:
            alterados = FluxoService.apontar(etapa, request.user, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            if alterados:
                messages.success(
                    request,
                    f'{etapa.get_etapa_display()}: {etapa.get_status_display()}.',
                )
            else:
                messages.info(request, 'Nada mudou.')

        return redirect(reverse('moda:fluxo-ordem', args=[ordem.pk]))
