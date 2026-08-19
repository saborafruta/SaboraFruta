"""
Telas da Ordem de Produção.

A OP fica no grupo PCP (`pcp/ordens-producao`), que é o endereço que o menu
já apontava. Ela nasce do pedido: não há tela de "nova ordem" em branco —
uma OP sem pedido não teria cliente, grade, ficha nem roteiro, que é
justamente tudo o que ela mostra.
"""
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import OrdemProducao, PedidoProducao
from .services.ficha_pdf import FichaProducaoPdfService
from .services.ordem import CAMPOS, OrdemProducaoService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _ordem_da_filial(request, pk) -> OrdemProducao:
    """
    Carrega a ordem com tudo que a tela lê das origens.

    A cadeia de `select_related`/`prefetch_related` é comprida porque a tela
    é comprida: sem ela, exibir uma ordem custaria dezenas de consultas —
    ficha, materiais, roteiro, operações, grade e personalizações, cada uma
    numa ida ao banco.
    """
    return get_object_or_404(
        OrdemProducao.objects.for_filial(_filial(request)).select_related(
            'pedido', 'pedido__cliente', 'item', 'item__produto',
            'item__produto__ficha', 'item__produto__roteiro',
            'item__modelo', 'item__cor', 'item__tecido', 'emitida_por',
        ).prefetch_related(
            'item__grade__tamanho',
            'item__personalizacoes',
            'item__visuais__mockup',
            'item__individuais__tamanho',
            'item__produto__ficha__materiais',
            'item__produto__roteiro__etapas__operacao',
        ),
        pk=pk,
    )


class OrdemListView(ModaBaseView):
    """Lista das ordens. Entregue no endereço do menu (pcp/ordens-producao)."""

    def get(self, request):
        ordens = (
            OrdemProducao.objects.for_filial(_filial(request))
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
        )

        status = (request.GET.get('status') or '').strip()
        if status in OrdemProducao.Status.values:
            ordens = ordens.filter(status=status)

        busca = (request.GET.get('q') or '').strip()
        if busca:
            ordens = ordens.filter(numero__icontains=busca)

        return render(request, 'moda/ordem_list.html', {
            'title': 'Ordens de Produção',
            'ordens': ordens,
            'status_escolhido': status,
            'busca': busca,
            'status_choices': OrdemProducao.Status.choices,
        })


class OrdemDetailView(ModaBaseView):
    """A OP completa — o que desce para a fábrica."""

    def get(self, request, pk):
        ordem = _ordem_da_filial(request, pk)
        return render(request, 'moda/ordem_detail.html', self.contexto(request, ordem))

    @staticmethod
    def contexto(request, ordem) -> dict:
        editaveis = OrdemProducaoService.campos_editaveis(request.user)
        return {
            'title': ordem.numero,
            'ordem': ordem,
            'editaveis': editaveis,
            # A tela usa isto para decidir se mostra o formulário: sem
            # nenhum campo liberado, um formulário vazio só confundiria.
            'pode_editar_algo': bool(editaveis) and not ordem.encerrada,
            'campos_todos': sorted(CAMPOS),
            'proximos_status': OrdemProducaoService.proximos_status(ordem),
            'pode_cancelar': OrdemProducaoService.pode_cancelar(request.user),
            'divergencias': ordem.divergencias,
        }


class FichaProducaoPdfView(ModaBaseView):
    """
    A ficha de produção da OP, em PDF.

    `exportar` e não `ver`: esta folha sai do sistema e vai para a mão de
    faccionista e de terceiro. Quem pode consultar a OP na tela não
    necessariamente pode tirá-la de dentro de casa, e o sistema de
    permissões já separa as duas coisas.
    """

    permissao_acao = 'exportar'

    def get(self, request, pk):
        ordem = _ordem_da_filial(request, pk)
        base = f'{request.scheme}://{request.get_host()}'
        pdf = FichaProducaoPdfService.gerar(ordem, base_url=base)

        resposta = HttpResponse(pdf, content_type='application/pdf')
        # Inline: quem clica quer conferir antes de imprimir. O botão de
        # baixar da tela usa `download` e resolve o outro caso com a
        # mesma rota.
        resposta['Content-Disposition'] = (
            f'inline; filename="FICHA-{ordem.numero}.pdf"'
        )
        return resposta


class OrdemGerarView(ModaBaseView):
    """Emite as ordens de um pedido — o botão fica na tela do pedido."""

    permissao_acao = 'criar'

    def post(self, request, pedido_pk):
        pedido = get_object_or_404(
            PedidoProducao.objects.for_filial(_filial(request))
            .prefetch_related('itens'),
            pk=pedido_pk,
        )
        try:
            ordens = OrdemProducaoService.gerar_do_pedido(pedido, usuario=request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:pedido-detail', args=[pedido.pk]))

        numeros = ', '.join(o.numero for o in ordens)
        messages.success(
            request,
            f'{len(ordens)} ordem(ns) emitida(s): {numeros}.',
        )
        if len(ordens) == 1:
            return redirect(reverse('moda:ordem-detail', args=[ordens[0].pk]))
        return redirect(reverse('moda:ordem-list'))


class OrdemEditarView(ModaBaseView):
    """
    Grava os campos liberados para o perfil.

    A view não escolhe o que gravar — quem decide é o serviço, com base nas
    permissões. Assim a regra vale igual aqui e em qualquer outro caminho
    que venha a alterar uma ordem.
    """

    permissao_acao = 'editar'

    def post(self, request, pk):
        ordem = _ordem_da_filial(request, pk)
        try:
            alterados = OrdemProducaoService.aplicar(ordem, request.user, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            if alterados:
                messages.success(request, f'Atualizado: {", ".join(alterados)}.')
            else:
                messages.info(request, 'Nada mudou.')
        return redirect(reverse('moda:ordem-detail', args=[ordem.pk]))


class OrdemStatusView(ModaBaseView):
    permissao_acao = 'ver'  # a permissão real é conferida no serviço

    def post(self, request, pk):
        ordem = _ordem_da_filial(request, pk)
        try:
            OrdemProducaoService.mudar_status(
                ordem, (request.POST.get('status') or '').strip(), request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'{ordem.numero}: {ordem.get_status_display()}.')
        return redirect(reverse('moda:ordem-detail', args=[ordem.pk]))
