"""CRUD de Ficha Técnica com formset de itens."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.tenant_context import tenant_atomic
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService
from apps.producao.forms import FichaTecnicaForm, ItemFichaTecnicaFormSet
from apps.producao.models import FichaTecnica


def _tabelas_ficha_tecnica_disponiveis():
    tabelas = set(connection.introspection.table_names())
    return {
        'producao_fichas_tecnicas',
        'producao_itens_ficha_tecnica',
    }.issubset(tabelas)


def _pagina_vazia(request):
    page_obj = Paginator([], 25).get_page(request.GET.get('page'))
    return page_obj


class FichaTecnicaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'producao'
    template_name = 'producao/ficha_tecnica/list.html'

    def get(self, request):
        busca = request.GET.get('q', '').strip()
        produto_id = request.GET.get('produto_id', '').strip()
        filial = getattr(request, 'filial_ativa', None)
        if not filial or not _tabelas_ficha_tecnica_disponiveis():
            page_obj = _pagina_vazia(request)
            return render(request, self.template_name, {
                'page_obj': page_obj,
                'fichas': page_obj.object_list,
                'busca': busca,
                'produto_id': produto_id,
            })

        qs = FichaTecnica.objects.for_filial(filial).select_related(
            'produto_acabado',
        )
        if produto_id.isdigit():
            qs = qs.filter(produto_acabado_id=int(produto_id))
        if busca:
            qs = qs.filter(
                Q(descricao__icontains=busca)
                | Q(produto_acabado__descricao__icontains=busca)
                | Q(codigo__icontains=busca)
            )
        page_obj = Paginator(qs.order_by('-created_at'), 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'fichas': page_obj.object_list,
            'busca': busca,
            'produto_id': produto_id,
        })


class FichaTecnicaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'producao'
    permissao_acao = 'criar'
    template_name = 'producao/ficha_tecnica/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': FichaTecnicaForm(),
            'formset': ItemFichaTecnicaFormSet(),
            'title': 'Nova Ficha Técnica',
            'cancel_url': reverse_lazy('producao:ficha-list'),
        })

    @tenant_atomic
    def post(self, request):
        form = FichaTecnicaForm(request.POST)
        formset = ItemFichaTecnicaFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            ficha = form.save(commit=False)
            ficha.filial = request.filial_ativa
            ficha.save()
            formset.instance = ficha
            formset.save()
            ReplicacaoProdutoService.sincronizar_ficha_tecnica(ficha)
            messages.success(request, f'Ficha "{ficha}" criada.')
            return redirect('producao:ficha-update', pk=ficha.pk)
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'title': 'Nova Ficha Técnica',
            'cancel_url': reverse_lazy('producao:ficha-list'),
        })


class FichaTecnicaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'producao'
    permissao_acao = 'editar'
    template_name = 'producao/ficha_tecnica/form.html'

    def get(self, request, pk):
        ficha = get_object_or_404(FichaTecnica.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, {
            'form': FichaTecnicaForm(instance=ficha),
            'formset': ItemFichaTecnicaFormSet(instance=ficha),
            'ficha': ficha,
            'title': f'Editar — {ficha}',
            'cancel_url': reverse_lazy('producao:ficha-list'),
        })

    @tenant_atomic
    def post(self, request, pk):
        ficha = get_object_or_404(FichaTecnica.objects.for_filial(request.filial_ativa), pk=pk)
        form = FichaTecnicaForm(request.POST, instance=ficha)
        formset = ItemFichaTecnicaFormSet(request.POST, instance=ficha)
        if form.is_valid() and formset.is_valid():
            ficha = form.save()
            formset.save()
            ReplicacaoProdutoService.sincronizar_ficha_tecnica(ficha)
            messages.success(request, 'Ficha técnica atualizada.')
            return redirect('producao:ficha-list')
        return render(request, self.template_name, {
            'form': form,
            'formset': formset,
            'ficha': ficha,
            'title': f'Editar — {ficha}',
            'cancel_url': reverse_lazy('producao:ficha-list'),
        })
