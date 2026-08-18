"""
Controle de qualidade (item Qualidade do grupo Produção).

A tela de uma inspeção grava em dois momentos: o checklist e a decisão. São
dois botões porque são dois atos — o inspetor confere a peça andando pela
mesa e só depois decide o que fazer com o lote. Um botão só obrigaria a
decidir antes de terminar de olhar.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import Inspecao, ItemInspecao, OrdemProducao
from .services.qualidade import QualidadeService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


def _inspecao(request, pk) -> Inspecao:
    return get_object_or_404(
        Inspecao.objects.for_filial(_filial(request)).select_related(
            'ordem', 'ordem__pedido', 'ordem__pedido__cliente', 'ordem__item',
        ).prefetch_related('itens', 'ordem__etapas'),
        pk=pk,
    )


class QualidadeListView(ModaBaseView):
    """As inspeções e os indicadores do setor."""

    def get(self, request):
        inspecoes = list(
            Inspecao.objects.for_filial(_filial(request))
            .select_related('ordem', 'ordem__pedido__cliente', 'ordem__item')
            .prefetch_related('itens')
        )

        status = (request.GET.get('status') or '').strip()
        visiveis = [i for i in inspecoes if i.status == status] if status in Inspecao.Status.values else inspecoes

        # Ordens que passaram do acabamento e ainda não têm inspeção aberta.
        abertas = {i.ordem_id for i in inspecoes if not i.encerrada}
        candidatas = [
            o for o in OrdemProducao.objects.for_filial(_filial(request))
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .select_related('pedido__cliente', 'item')
            .prefetch_related('etapas')
            if o.pk not in abertas and self._pronta_para_inspecao(o)
        ]

        return render(request, 'moda/qualidade_list.html', {
            'title': 'Controle de Qualidade',
            'inspecoes': visiveis,
            'candidatas': candidatas,
            'indicadores': QualidadeService.indicadores(inspecoes),
            'status_escolhido': status,
            'status_choices': Inspecao.Status.choices,
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })

    @staticmethod
    def _pronta_para_inspecao(ordem) -> bool:
        """
        Chegou à qualidade: tudo antes dela encerrado.

        Oferecer inspeção de peça que ainda está na costura faria o inspetor
        abrir uma ficha sem ter o que conferir.
        """
        etapas = list(ordem.etapas.all())
        qualidade = next((e for e in etapas if e.etapa == 'qualidade'), None)
        if qualidade is None or qualidade.encerrada:
            return False
        return not any(
            e.sequencia < qualidade.sequencia and not e.encerrada for e in etapas
        )


class InspecaoCriarView(ModaBaseView):
    permissao_acao = 'criar'

    def post(self, request, pk):
        ordem = get_object_or_404(
            OrdemProducao.objects.for_filial(_filial(request)).prefetch_related('etapas'),
            pk=pk,
        )
        try:
            inspecao = QualidadeService.criar(_filial(request), ordem, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:qualidade-list'))

        messages.success(request, f'Inspeção #{inspecao.numero:04d} aberta.')
        return redirect(reverse('moda:inspecao-detail', args=[inspecao.pk]))


class InspecaoDetailView(ModaBaseView):
    def get(self, request, pk):
        inspecao = _inspecao(request, pk)
        return render(request, 'moda/inspecao_detail.html', {
            'title': f'Inspeção #{inspecao.numero:04d}',
            'inspecao': inspecao,
            'itens': inspecao.itens.all(),
            'resultados': ItemInspecao.Resultado.choices,
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })


class InspecaoChecklistView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        inspecao = _inspecao(request, pk)
        try:
            alterados = QualidadeService.avaliar(inspecao, request.POST)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            if alterados:
                messages.success(request, f'{alterados} ponto(s) do checklist gravado(s).')
            else:
                messages.info(request, 'Nada mudou.')
        return redirect(reverse('moda:inspecao-detail', args=[inspecao.pk]))


class InspecaoDecidirView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        inspecao = _inspecao(request, pk)
        try:
            QualidadeService.decidir(
                inspecao, (request.POST.get('status') or '').strip(),
                request.POST, request.user,
            )
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(
                request,
                f'Inspeção #{inspecao.numero:04d}: {inspecao.get_status_display()} '
                f'— {inspecao.percentual_aprovacao}% de aprovação.',
            )
        return redirect(reverse('moda:inspecao-detail', args=[inspecao.pk]))


class InspecaoAplicarView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        inspecao = _inspecao(request, pk)
        try:
            resultado = QualidadeService.aplicar_no_fluxo(inspecao, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, f'Resultado levado ao fluxo — {resultado}.')
        return redirect(reverse('moda:inspecao-detail', args=[inspecao.pk]))
