"""Telas da viagem: lista, criação, edição e mudança de etapa."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.exceptions import DadosInvalidosError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.logistica.forms_viagem import ViagemForm
from apps.logistica.models import Viagem
from apps.logistica.services.viagem import ViagemService


def _filial(request):
    return request.filial_ativa


class ViagemListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/list.html'

    def get(self, request):
        filial = _filial(request)
        busca = (request.GET.get('q') or '').strip()
        status = (request.GET.get('status') or '').strip()

        viagens = (
            Viagem.objects.for_filial(filial)
            .select_related('motorista', 'veiculo', 'responsavel', 'vendedor')
            .annotate(qtd_itens=Count('itens'))
            # ORDEM EXPLICITA: `annotate` derruba a ordenacao do Meta, e paginar
            # sem ordem deixa a mesma viagem aparecer duas vezes entre paginas.
            .order_by('-data_saida', '-numero')
        )
        if busca:
            viagens = viagens.filter(
                Q(numero__icontains=busca)
                | Q(motorista_nome__icontains=busca)
                | Q(veiculo_placa__icontains=busca)
                | Q(rota__icontains=busca)
            )
        if status:
            viagens = viagens.filter(status=status)

        pagina = Paginator(viagens, 30).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'title': 'Viagens',
            'viagens': pagina.object_list,
            'page_obj': pagina,
            'busca': busca,
            'status_filtro': status,
            'status_choices': Viagem.Status.choices,
            'kpis': cls_kpis(filial),
            'pode_agir': request.user.tem_permissao('logistica', 'criar'),
        })


def cls_kpis(filial) -> dict:
    """Os números que a lista mostra no topo."""
    base = Viagem.objects.for_filial(filial)
    na_estrada = base.filter(status__in=(
        Viagem.Status.EM_TRANSITO, Viagem.Status.EM_VENDAS, Viagem.Status.RETORNANDO,
    ))
    return {
        'total': base.count(),
        'na_estrada': na_estrada.count(),
        'aguardando_documentos': base.filter(
            status=Viagem.Status.AGUARDANDO_DOCUMENTOS,
        ).count(),
        'aguardando_conferencia': base.filter(
            status=Viagem.Status.AGUARDANDO_CONFERENCIA,
        ).count(),
        'em_poder': base.aggregate(
            total=Sum('saldos__quantidade_remetida'),
        )['total'] or 0,
    }


class ViagemCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'criar'
    template_name = 'logistica/viagem/form.html'

    def get(self, request):
        filial = _filial(request)
        return render(request, self.template_name, {
            'title': 'Nova Viagem',
            'form': ViagemForm(filial=filial, initial={
                'uf_origem': (getattr(filial, 'uf', '') or '').upper(),
                'responsavel': request.user.pk,
            }),
            # O NUMERO E' MOSTRADO, NAO PEDIDO: numero repetido bate na unique
            # depois de a pessoa ja' ter preenchido tudo.
            'proximo_numero': ViagemService.proximo_numero(filial),
            'cancel_url': reverse('logistica:viagem-list'),
        })

    def post(self, request):
        filial = _filial(request)
        form = ViagemForm(request.POST, filial=filial)
        if form.is_valid():
            viagem = form.save(commit=False)
            viagem.filial = filial
            viagem.numero = ViagemService.proximo_numero(filial)
            if not viagem.responsavel_id:
                viagem.responsavel = request.user
            viagem.save()
            messages.success(request, f'Viagem #{viagem.numero:06d} criada.')
            return redirect('logistica:viagem-detail', pk=viagem.pk)
        messages.error(request, 'Revise os dados da viagem.')
        return render(request, self.template_name, {
            'title': 'Nova Viagem',
            'form': form,
            'proximo_numero': ViagemService.proximo_numero(filial),
            'cancel_url': reverse('logistica:viagem-list'),
        })


class ViagemUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'
    template_name = 'logistica/viagem/form.html'

    def _viagem(self, request, pk):
        return get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)

    def get(self, request, pk):
        viagem = self._viagem(request, pk)
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'form': ViagemForm(instance=viagem, filial=_filial(request)),
            'viagem': viagem,
            'proximo_numero': viagem.numero,
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })

    def post(self, request, pk):
        viagem = self._viagem(request, pk)
        form = ViagemForm(request.POST, instance=viagem, filial=_filial(request))
        if form.is_valid():
            form.save()
            messages.success(request, 'Viagem atualizada.')
            return redirect('logistica:viagem-detail', pk=viagem.pk)
        messages.error(request, 'Revise os dados da viagem.')
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'form': form, 'viagem': viagem, 'proximo_numero': viagem.numero,
            'cancel_url': reverse('logistica:viagem-detail', args=[viagem.pk]),
        })


class ViagemDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    template_name = 'logistica/viagem/detail.html'

    def get(self, request, pk):
        viagem = get_object_or_404(
            Viagem.objects.for_filial(_filial(request))
            .select_related('motorista', 'veiculo', 'responsavel', 'vendedor'),
            pk=pk,
        )
        return render(request, self.template_name, {
            'title': f'Viagem #{viagem.numero:06d}',
            'viagem': viagem,
            'resumo': ViagemService.resumo(viagem),
            'itens': viagem.itens.select_related('natureza', 'produto', 'cliente'),
            'entregas': ViagemService.entregas_por_cliente(viagem),
            'conciliacao': ViagemService.conciliacao(viagem),
            'proximos_status': viagem.proximos_status(),
            'pendencias': (
                ViagemService.conferir_antes_de_fechar(viagem)
                if viagem.editavel else []
            ),
            'pode_agir': request.user.tem_permissao('logistica', 'editar'),
        })


class ViagemMudarStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.mudar_status(
                viagem, (request.POST.get('status') or '').strip(), usuario=request.user,
            )
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, f'Viagem em {viagem.get_status_display().lower()}.')
        return volta


class ViagemFecharCargaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.fechar_carga(viagem, usuario=request.user)
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(
            request,
            'Carga fechada: a mercadoria saiu do estoque e os documentos '
            'estão pendentes de emissão.',
        )
        return volta


class ViagemCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'logistica'
    permissao_acao = 'editar'

    def post(self, request, pk):
        viagem = get_object_or_404(Viagem.objects.for_filial(_filial(request)), pk=pk)
        volta = redirect('logistica:viagem-detail', pk=viagem.pk)
        try:
            ViagemService.cancelar(viagem, motivo=(request.POST.get('motivo') or '').strip())
        except DadosInvalidosError as erro:
            messages.error(request, str(erro))
            return volta
        messages.success(request, 'Viagem cancelada.')
        return volta
