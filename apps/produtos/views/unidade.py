"""CRUD de Unidade de Medida."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.tenant_context import tenant_atomic
from apps.produtos.forms import UnidadeMedidaForm
from apps.produtos.models import UnidadeMedida
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


class UnidadeListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    template_name = 'produtos/unidade/list.html'

    def get(self, request):
        qs = UnidadeMedida.objects.for_filial(request.filial_ativa).filter(
            empresa=request.user.empresa,
        ).order_by('sigla')
        page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'page_obj': page_obj,
            'unidades': page_obj.object_list,
        })


class UnidadeCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'
    template_name = 'produtos/unidade/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': UnidadeMedidaForm(),
            'title': 'Nova Unidade',
            'cancel_url': reverse_lazy('produtos:unidade-list'),
        })

    def post(self, request):
        form = UnidadeMedidaForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.empresa = request.user.empresa
            obj.save()
            ReplicacaoProdutoService.sincronizar_unidade(obj, request.filial_ativa)
            messages.success(request, f'Unidade "{obj}" criada.')
            return redirect('produtos:unidade-list')
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Unidade',
            'cancel_url': reverse_lazy('produtos:unidade-list'),
        })


class UnidadeInlineCreateView(PermissaoRequiredMixin, View):
    """
    Mesma criação de `UnidadeCreateView`, só que sem sair da tela que
    pediu -- um formulário de produto no meio do preenchimento (nome,
    quantidade...) não deveria perder tudo isso só porque a unidade que
    faltava ainda não existia. Devolve JSON em vez de redirecionar,
    pra quem chamou inserir a opção nova no próprio `<select>` e seguir
    preenchendo.
    """

    permissao_modulo = 'produtos'
    permissao_acao = 'criar'

    def post(self, request):
        dados = request.POST.copy()
        # O modal enxuto só pede sigla e descrição -- os demais campos do
        # form completo (tipo, ativo) já são opcionais, mas
        # `fator_conversao_base` é obrigatório no model (`default=1` não
        # dispensa o form de pedir o valor) e não tem widget nenhum aqui.
        dados.setdefault('fator_conversao_base', '1')
        form = UnidadeMedidaForm(dados)
        if not form.is_valid():
            erro = ' '.join(
                f'{campo}: {", ".join(erros)}' for campo, erros in form.errors.items()
            )
            return JsonResponse({'ok': False, 'error': erro or 'Dados inválidos.'}, status=400)

        obj = form.save(commit=False)
        obj.empresa = request.user.empresa
        # `empresa` não é campo do form (só é atribuído aqui), então a
        # validação automática de `unique_together` do ModelForm não pega
        # sigla repetida -- só o banco pega, no save. Sem o try/except essa
        # tela quebraria com 500 em vez de devolver o erro pro modal.
        try:
            with tenant_atomic():
                obj.save()
        except IntegrityError:
            return JsonResponse(
                {'ok': False, 'error': f'Já existe uma unidade com a sigla "{obj.sigla}".'},
                status=400,
            )
        ReplicacaoProdutoService.sincronizar_unidade(obj, request.filial_ativa)
        return JsonResponse({'ok': True, 'id': obj.pk, 'label': str(obj)})


class UnidadeUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'
    template_name = 'produtos/unidade/form.html'

    def get(self, request, pk):
        obj = get_object_or_404(
            UnidadeMedida.objects.for_filial(request.filial_ativa),
            pk=pk,
            empresa=request.user.empresa,
        )
        return render(request, self.template_name, {
            'form': UnidadeMedidaForm(instance=obj),
            'title': f'Editar — {obj}',
            'cancel_url': reverse_lazy('produtos:unidade-list'),
        })

    def post(self, request, pk):
        obj = get_object_or_404(
            UnidadeMedida.objects.for_filial(request.filial_ativa),
            pk=pk,
            empresa=request.user.empresa,
        )
        form = UnidadeMedidaForm(request.POST, instance=obj)
        if form.is_valid():
            obj = form.save()
            ReplicacaoProdutoService.sincronizar_unidade(obj, request.filial_ativa)
            messages.success(request, 'Unidade atualizada.')
            return redirect('produtos:unidade-list')
        return render(request, self.template_name, {
            'form': form,
            'title': f'Editar — {obj}',
            'cancel_url': reverse_lazy('produtos:unidade-list'),
        })
