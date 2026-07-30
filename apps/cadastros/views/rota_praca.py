"""CRUD de Praça e Rota."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View

from apps.cadastros.forms import PracaForm, RotaForm
from apps.cadastros.models import Praca, Rota
from apps.core.services.permissions import PermissaoRequiredMixin


# ─── PRAÇA ────────────────────────────────────────────────────────────────────

class PracaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    template_name = 'cadastros/praca/list.html'

    def get(self, request):
        qs = Praca.objects.for_filial(request.filial_ativa)
        ativo = request.GET.get('ativo', '1')
        busca = request.GET.get('q', '').strip()
        if ativo == '0':
            qs = qs.filter(ativo=False)
        else:
            qs = qs.filter(ativo=True)
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(codigo__icontains=busca)
                | Q(uf__icontains=busca)
                | Q(cidades__icontains=busca)
            )
        page_obj = Paginator(qs.order_by('nome'), 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'pracas': page_obj.object_list,
            'page_obj': page_obj,
            'busca': busca,
            'ativo': ativo,
        })


class PracaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'
    template_name = 'cadastros/praca/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': PracaForm(filial=request.filial_ativa),
            'title': 'Nova Praça',
            'cancel_url': reverse_lazy('cadastros:praca-list'),
        })

    def post(self, request):
        form = PracaForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.filial = request.filial_ativa
            obj.save()
            messages.success(request, f'Praça "{obj.nome}" criada com sucesso.')
            return redirect('cadastros:praca-list')
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Praça',
            'cancel_url': reverse_lazy('cadastros:praca-list'),
        })


class PracaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    template_name = 'cadastros/praca/form.html'

    def get(self, request, pk):
        obj = get_object_or_404(Praca.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, {
            'form': PracaForm(instance=obj, filial=request.filial_ativa),
            'praca': obj,
            'title': f'Editar — {obj.nome}',
            'cancel_url': reverse_lazy('cadastros:praca-list'),
        })

    def post(self, request, pk):
        obj = get_object_or_404(Praca.objects.for_filial(request.filial_ativa), pk=pk)
        form = PracaForm(request.POST, instance=obj, filial=request.filial_ativa)
        if form.is_valid():
            form.save()
            messages.success(request, f'Praça "{obj.nome}" atualizada.')
            return redirect('cadastros:praca-list')
        return render(request, self.template_name, {
            'form': form,
            'praca': obj,
            'title': f'Editar — {obj.nome}',
            'cancel_url': reverse_lazy('cadastros:praca-list'),
        })


class PracaToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        obj = get_object_or_404(Praca.objects.for_filial(request.filial_ativa), pk=pk)
        obj.ativo = not obj.ativo
        obj.save(update_fields=['ativo', 'updated_at'])
        status = 'ativada' if obj.ativo else 'desativada'
        messages.success(request, f'Praça "{obj.nome}" {status}.')
        return redirect('cadastros:praca-list')


# ─── ROTA ─────────────────────────────────────────────────────────────────────

class RotaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    template_name = 'cadastros/rota/list.html'

    def get(self, request):
        qs = Rota.objects.for_filial(request.filial_ativa)
        ativo = request.GET.get('ativo', '1')
        busca = request.GET.get('q', '').strip()
        if ativo == '0':
            qs = qs.filter(ativo=False)
        else:
            qs = qs.filter(ativo=True)
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(codigo__icontains=busca)
                | Q(descricao__icontains=busca)
                | Q(motorista__nome__icontains=busca)
                | Q(veiculo__placa__icontains=busca)
                | Q(motorista_padrao__icontains=busca)
                | Q(veiculo_padrao__icontains=busca)
            )
        page_obj = Paginator(
            qs.select_related('motorista', 'veiculo').prefetch_related('pracas')
              .distinct().order_by('nome'), 25).get_page(request.GET.get('page'))
        return render(request, self.template_name, {
            'rotas': page_obj.object_list,
            'page_obj': page_obj,
            'busca': busca,
            'ativo': ativo,
        })


class RotaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'
    template_name = 'cadastros/rota/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': RotaForm(filial=request.filial_ativa),
            'title': 'Nova Rota',
            'cancel_url': reverse_lazy('cadastros:rota-list'),
        })

    def post(self, request):
        form = RotaForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.filial = request.filial_ativa
            obj.save()
            form.save_m2m()
            messages.success(request, f'Rota "{obj.nome}" criada com sucesso.')
            return redirect('cadastros:rota-list')
        return render(request, self.template_name, {
            'form': form,
            'title': 'Nova Rota',
            'cancel_url': reverse_lazy('cadastros:rota-list'),
        })


class RotaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    template_name = 'cadastros/rota/form.html'

    def get(self, request, pk):
        obj = get_object_or_404(Rota.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, {
            'form': RotaForm(instance=obj, filial=request.filial_ativa),
            'rota': obj,
            'title': f'Editar — {obj.nome}',
            'cancel_url': reverse_lazy('cadastros:rota-list'),
        })

    def post(self, request, pk):
        obj = get_object_or_404(Rota.objects.for_filial(request.filial_ativa), pk=pk)
        form = RotaForm(request.POST, instance=obj, filial=request.filial_ativa)
        if form.is_valid():
            form.save()
            messages.success(request, f'Rota "{obj.nome}" atualizada.')
            return redirect('cadastros:rota-list')
        return render(request, self.template_name, {
            'form': form,
            'rota': obj,
            'title': f'Editar — {obj.nome}',
            'cancel_url': reverse_lazy('cadastros:rota-list'),
        })


class RotaToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        obj = get_object_or_404(Rota.objects.for_filial(request.filial_ativa), pk=pk)
        obj.ativo = not obj.ativo
        obj.save(update_fields=['ativo', 'updated_at'])
        status = 'ativada' if obj.ativo else 'desativada'
        messages.success(request, f'Rota "{obj.nome}" {status}.')
        return redirect('cadastros:rota-list')
