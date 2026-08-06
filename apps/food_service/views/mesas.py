"""CRUD de cadastro de mesas."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin

from ..models import Mesa


class MesaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        mesas = Mesa.objects.for_filial(request.filial_ativa).order_by('numero')
        return render(request, 'food_service/mesa_list.html', {
            'title': 'Mesas',
            'mesas': mesas,
        })


class _MesaFormMixin:
    template_name = 'food_service/mesa_form.html'

    def _contexto(self, request, mesa=None):
        return {
            'title': 'Editar Mesa' if mesa else 'Nova Mesa',
            'mesa': mesa,
            'cancel_url': reverse('food_service:mesa-list'),
        }

    def _salvar(self, request, mesa):
        numero = request.POST.get('numero', '').strip()
        if not numero.isdigit():
            messages.error(request, 'Informe um número de mesa válido.')
            return None

        capacidade = request.POST.get('capacidade', '4').strip()
        if not capacidade.isdigit():
            capacidade = '4'

        if mesa is None:
            mesa = Mesa(filial=request.filial_ativa)
        mesa.numero = int(numero)
        mesa.nome = request.POST.get('nome', '').strip()
        mesa.capacidade = int(capacidade)
        mesa.setor = request.POST.get('setor', '').strip()
        mesa.observacoes = request.POST.get('observacoes', '').strip()
        mesa.save()
        return mesa


class MesaCreateView(_MesaFormMixin, PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'criar'

    def get(self, request):
        return render(request, self.template_name, self._contexto(request))

    def post(self, request):
        mesa = self._salvar(request, None)
        if mesa is None:
            return render(request, self.template_name, self._contexto(request))
        messages.success(request, f'Mesa "{mesa}" criada.')
        return redirect(reverse('food_service:mesa-list'))


class MesaUpdateView(_MesaFormMixin, PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def get(self, request, pk):
        mesa = get_object_or_404(Mesa.objects.for_filial(request.filial_ativa), pk=pk)
        return render(request, self.template_name, self._contexto(request, mesa))

    def post(self, request, pk):
        mesa = get_object_or_404(Mesa.objects.for_filial(request.filial_ativa), pk=pk)
        mesa = self._salvar(request, mesa)
        if mesa is None:
            return render(request, self.template_name, self._contexto(request, mesa))
        messages.success(request, f'Mesa "{mesa}" atualizada.')
        return redirect(reverse('food_service:mesa-list'))


class MesaToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'editar'

    def post(self, request, pk):
        mesa = get_object_or_404(Mesa.objects.for_filial(request.filial_ativa), pk=pk)
        mesa.ativo = not mesa.ativo
        mesa.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, f'Mesa {"ativada" if mesa.ativo else "desativada"}.')
        return redirect(reverse('food_service:mesa-list'))


class MesaDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'excluir'

    def post(self, request, pk):
        mesa = get_object_or_404(Mesa.objects.for_filial(request.filial_ativa), pk=pk)
        if mesa.comandas.exists():
            messages.error(request, 'Mesa possui comandas vinculadas e não pode ser excluída — desative-a.')
            return redirect(reverse('food_service:mesa-list'))
        nome = str(mesa)
        mesa.delete()
        messages.success(request, f'Mesa "{nome}" excluída.')
        return redirect(reverse('food_service:mesa-list'))
