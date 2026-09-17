"""CRUD de Marca / Fabricante de produto."""
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.produtos.forms import MarcaProdutoForm
from apps.produtos.models import MarcaProduto
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


def _sincronizar_marca_sem_quebrar(request, marca):
    try:
        ReplicacaoProdutoService._vincular_marca(marca, marca.filial)
        ReplicacaoProdutoService.sincronizar_marca(marca)
    except Exception:
        messages.warning(
            request,
            'Marca salva, mas nao foi possivel replicar para outras filiais agora.',
        )


def _duplicidade_marca(form, empresa, filial, instance=None):
    nome = form.cleaned_data.get('nome', '').strip()
    if not nome:
        return False
    qs = MarcaProduto.objects.for_filial(filial).filter(
        empresa=empresa,
        nome__iexact=nome,
    )
    if instance and instance.pk:
        qs = qs.exclude(pk=instance.pk)
    if qs.exists():
        form.add_error('nome', 'Ja existe uma marca / fabricante com esse nome nesta filial.')
        return True
    return False


class MarcaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'ver'
    template_name = 'produtos/marca/list.html'

    def get(self, request):
        marcas = MarcaProduto.objects.for_filial(request.filial_ativa).filter(
            empresa=empresa_operacional(request),
        )
        busca = request.GET.get('q', '').strip()
        if busca:
            marcas = marcas.filter(Q(nome__icontains=busca) | Q(descricao__icontains=busca))
        return render(request, self.template_name, {
            'marcas': marcas.order_by('nome'),
            'busca': busca,
        })


class MarcaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'criar'
    template_name = 'produtos/marca/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': MarcaProdutoForm(),
            'title': 'Nova Marca / Fabricante',
            'cancel_url': reverse_lazy('produtos:marca-list'),
        })

    def post(self, request):
        form = MarcaProdutoForm(request.POST)
        if form.is_valid():
            if _duplicidade_marca(form, empresa_operacional(request), request.filial_ativa):
                return render(request, self.template_name, {
                    'form': form,
                    'title': 'Nova Marca / Fabricante',
                    'cancel_url': reverse_lazy('produtos:marca-list'),
                })
            marca = form.save(commit=False)
            marca.empresa = empresa_operacional(request)
            marca.filial = request.filial_ativa
            try:
                marca.save()
            except IntegrityError:
                form.add_error('nome', 'Ja existe uma marca / fabricante com esse nome nesta filial.')
                return render(request, self.template_name, {
                    'form': form,
                    'title': 'Nova Marca / Fabricante',
                    'cancel_url': reverse_lazy('produtos:marca-list'),
                })
            _sincronizar_marca_sem_quebrar(request, marca)
            messages.success(request, f'Marca / fabricante "{marca}" criada.')
            return redirect('produtos:marca-list')
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Marca / Fabricante',
            'cancel_url': reverse_lazy('produtos:marca-list'),
        })


class MarcaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'produtos'
    permissao_acao = 'editar'
    template_name = 'produtos/marca/form.html'

    def get(self, request, pk):
        marca = get_object_or_404(
            MarcaProduto.objects.for_filial(request.filial_ativa), pk=pk, empresa=empresa_operacional(request),
        )
        return render(request, self.template_name, {
            'form': MarcaProdutoForm(instance=marca),
            'title': f'Editar - {marca}',
            'cancel_url': reverse_lazy('produtos:marca-list'),
        })

    def post(self, request, pk):
        marca = get_object_or_404(
            MarcaProduto.objects.for_filial(request.filial_ativa), pk=pk, empresa=empresa_operacional(request),
        )
        form = MarcaProdutoForm(request.POST, instance=marca)
        if form.is_valid():
            if _duplicidade_marca(form, empresa_operacional(request), request.filial_ativa, marca):
                return render(request, self.template_name, {
                    'form': form,
                    'title': f'Editar - {marca}',
                    'cancel_url': reverse_lazy('produtos:marca-list'),
                })
            marca = form.save(commit=False)
            marca.empresa = empresa_operacional(request)
            marca.filial = request.filial_ativa
            try:
                marca.save()
            except IntegrityError:
                form.add_error('nome', 'Ja existe uma marca / fabricante com esse nome nesta filial.')
                return render(request, self.template_name, {
                    'form': form,
                    'title': f'Editar - {marca}',
                    'cancel_url': reverse_lazy('produtos:marca-list'),
                })
            _sincronizar_marca_sem_quebrar(request, marca)
            messages.success(request, 'Marca / fabricante atualizada.')
            return redirect('produtos:marca-list')
        return render(request, self.template_name, {
            'form': form,
            'title': f'Editar - {marca}',
            'cancel_url': reverse_lazy('produtos:marca-list'),
        })
