"""
Necessidade de materiais e requisições (grupo PCP).

A tela recalcula a cada carga, em vez de guardar o resultado: necessidade é
uma foto de ordens abertas × ficha × estoque, e as três mudam o tempo todo.
Um número gravado aqui estaria velho antes de alguém abrir a página.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.services.exceptions import DomainError

from .models import RequisicaoMaterial, ReservaMaterial
from .services.necessidade import NecessidadeService
from .views import ModaBaseView


def _filial(request):
    return request.filial_ativa


class NecessidadeView(ModaBaseView):
    def get(self, request):
        linhas = NecessidadeService.calcular(_filial(request))
        return render(request, 'moda/necessidade.html', {
            'title': 'Necessidade de Materiais',
            'linhas': linhas,
            'resumo': NecessidadeService.resumo(linhas),
            'requisicoes': RequisicaoMaterial.objects.for_filial(_filial(request))
                .prefetch_related('itens')[:10],
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
        })


class ReservarView(ModaBaseView):
    """Separa o material de uma linha da necessidade."""

    permissao_acao = 'editar'

    def post(self, request):
        filial = _filial(request)
        chave = (request.POST.get('chave') or '').strip()

        linha = next(
            (l for l in NecessidadeService.calcular(filial) if l.chave == chave), None,
        )
        if linha is None:
            messages.error(request, 'Material não encontrado na necessidade atual.')
            return redirect(reverse('moda:necessidade'))

        try:
            reservas = NecessidadeService.reservar(filial, linha, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            total = sum(r.quantidade for r in reservas)
            messages.success(
                request,
                f'{total} {linha.unidade} de {linha.descricao} reservado(s) '
                f'em {len(reservas)} ordem(ns).',
            )
        return redirect(reverse('moda:necessidade'))


class CancelarReservaView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        reserva = get_object_or_404(
            ReservaMaterial.objects.for_filial(_filial(request)), pk=pk,
        )
        try:
            NecessidadeService.cancelar_reserva(reserva, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, 'Reserva cancelada e material devolvido ao estoque livre.')
        return redirect(reverse('moda:necessidade'))


class RequisicaoGerarView(ModaBaseView):
    permissao_acao = 'criar'

    def post(self, request):
        filial = _filial(request)
        linhas = NecessidadeService.calcular(filial)

        try:
            requisicao = NecessidadeService.gerar_requisicao(filial, linhas, request.user)
        except DomainError as erro:
            messages.error(request, str(erro))
            return redirect(reverse('moda:necessidade'))

        messages.success(
            request,
            f'Requisição #{requisicao.numero:04d} criada com {requisicao.total_itens} '
            f'item(ns). Compras escolhe fornecedor e preço a partir dela.',
        )
        return redirect(reverse('moda:requisicao-detail', args=[requisicao.pk]))


class RequisicaoDetailView(ModaBaseView):
    def get(self, request, pk):
        requisicao = get_object_or_404(
            RequisicaoMaterial.objects.for_filial(_filial(request))
            .select_related('criado_por').prefetch_related('itens__produto'),
            pk=pk,
        )
        return render(request, 'moda/requisicao_detail.html', {
            'title': f'Requisição #{requisicao.numero:04d}',
            'requisicao': requisicao,
            'itens': requisicao.itens.all(),
            'pode_agir': request.user.tem_permissao('moda', 'editar'),
            'status_choices': RequisicaoMaterial.Status.choices,
        })


class RequisicaoStatusView(ModaBaseView):
    permissao_acao = 'editar'

    def post(self, request, pk):
        requisicao = get_object_or_404(
            RequisicaoMaterial.objects.for_filial(_filial(request)), pk=pk,
        )
        novo = (request.POST.get('status') or '').strip()

        if novo not in RequisicaoMaterial.Status.values:
            messages.error(request, 'Status inválido.')
        else:
            requisicao.status = novo
            requisicao.save(update_fields=['status'])
            messages.success(request, f'Requisição marcada como {requisicao.get_status_display()}.')

        return redirect(reverse('moda:requisicao-detail', args=[requisicao.pk]))
