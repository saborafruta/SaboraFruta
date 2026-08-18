"""
Terminais de setor (grupo Produção).

Uma view para os quatro setores. O apontamento delega ao `FluxoService`,
que é o mesmo caminho da tela de fluxo: duas rotas gravando a etapa com
regras próprias acabariam divergindo na primeira validação nova.
"""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import EtapaOrdem, OrdemProducao
from .services.fluxo import FluxoService
from .services.terminal import SETORES, TerminalService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


class TerminalView(ModaBaseView):
    """A fila de um setor, com o apontamento embutido em cada cartão."""

    def get(self, request, slug):
        setor = TerminalService.setor(slug)
        if setor is None:
            raise Http404('Setor sem terminal.')

        editaveis = FluxoService.campos_editaveis(request.user)
        return render(request, 'moda/terminal.html', {
            'title': f'Produção — {setor.titulo}',
            'setor': setor,
            'fila': TerminalService.fila(_filial(request), setor),
            'campos': TerminalService.campos_do_setor(setor),
            'editaveis': editaveis,
            'pode_apontar': bool(editaveis),
            'outros': [s for s in SETORES.values() if s.slug != setor.slug],
        })


class TerminalApontarView(ModaBaseView):
    """
    Grava o apontamento vindo do terminal.

    A permissão real é conferida campo a campo no serviço — por isso aqui
    basta 'ver': quem só vê não tem campo autorizado e nada é gravado.
    """

    permissao_acao = 'ver'

    def post(self, request, slug, pk):
        setor = TerminalService.setor(slug)
        if setor is None:
            raise Http404('Setor sem terminal.')

        ordem = get_object_or_404(
            OrdemProducao.objects.for_filial(_filial(request)).prefetch_related('etapas'),
            pk=pk,
        )
        etapa = get_object_or_404(EtapaOrdem, ordem=ordem, etapa=setor.etapa)

        # Só os campos deste setor: um POST forjado com `quantidade_planejada`
        # não passa por aqui, mesmo que o perfil pudesse alterá-la na tela de
        # fluxo. O terminal é uma porta estreita de propósito.
        permitidos = set(TerminalService.campos_do_setor(setor))
        dados = {k: v for k, v in request.POST.items() if k in permitidos}

        try:
            alterados = FluxoService.apontar(etapa, request.user, dados)
        except DomainError as erro:
            messages.error(request, f'{ordem.numero}: {erro}')
        else:
            if alterados:
                messages.success(
                    request,
                    f'{ordem.numero} — {etapa.get_status_display()}: '
                    f'{etapa.quantidade_produzida} de {etapa.planejada}.',
                )
            else:
                messages.info(request, 'Nada mudou.')

        return redirect(reverse('moda:terminal', args=[slug]))
