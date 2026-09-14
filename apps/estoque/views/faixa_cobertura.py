"""CRUD das faixas de classificação de cobertura de estoque (Ruptura...Excesso)."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.services.auditoria import registrar_auditoria, snapshot_modelo
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.forms import FaixaCoberturaEstoqueForm
from apps.estoque.models import FaixaCoberturaEstoque
from apps.estoque.views.permissoes import permissoes_estoque


def _auditar_faixa(request, acao, faixa, descricao='', antes=None, depois=None):
    return registrar_auditoria(
        request=request, modulo='estoque', acao=acao, objeto=faixa,
        descricao=descricao or f'Faixa de cobertura {faixa}',
        antes=antes, depois=depois,
    )


class FaixaCoberturaListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'
    template_name = 'estoque/faixa_cobertura/list.html'

    def get(self, request):
        empresa = empresa_operacional(request)
        faixas = list(
            FaixaCoberturaEstoque.objects.filter(empresa=empresa)
            .select_related('categoria', 'produto')
            .order_by('produto__descricao', 'categoria__nome')
        )
        return render(request, self.template_name, {
            'title': 'Faixas de cobertura de estoque',
            'faixas': faixas,
            'permissoes_estoque': permissoes_estoque(request),
        })


class FaixaCoberturaCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'criar'
    template_name = 'estoque/faixa_cobertura/form.html'

    def get(self, request):
        empresa = empresa_operacional(request)
        return render(request, self.template_name, {
            'form': FaixaCoberturaEstoqueForm(empresa=empresa),
            'title': 'Nova faixa de cobertura',
        })

    def post(self, request):
        empresa = empresa_operacional(request)
        form = FaixaCoberturaEstoqueForm(request.POST, empresa=empresa)
        if form.is_valid():
            faixa = form.save()
            _auditar_faixa(request, 'criar', faixa, f'Faixa de cobertura {faixa} criada', depois=snapshot_modelo(faixa))
            messages.success(request, 'Faixa de cobertura criada.')
            return redirect('estoque:faixa-cobertura-list')
        return render(request, self.template_name, {'form': form, 'title': 'Nova faixa de cobertura'})


class FaixaCoberturaUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'editar'
    template_name = 'estoque/faixa_cobertura/form.html'

    def _get(self, request, pk):
        empresa = empresa_operacional(request)
        return get_object_or_404(FaixaCoberturaEstoque.objects.filter(empresa=empresa), pk=pk)

    def get(self, request, pk):
        faixa = self._get(request, pk)
        empresa = empresa_operacional(request)
        return render(request, self.template_name, {
            'form': FaixaCoberturaEstoqueForm(instance=faixa, empresa=empresa),
            'faixa': faixa,
            'title': f'Editar faixa de cobertura — {faixa}',
        })

    def post(self, request, pk):
        faixa = self._get(request, pk)
        antes = snapshot_modelo(faixa)
        empresa = empresa_operacional(request)
        form = FaixaCoberturaEstoqueForm(request.POST, instance=faixa, empresa=empresa)
        if form.is_valid():
            faixa = form.save()
            _auditar_faixa(request, 'editar', faixa, f'Faixa de cobertura {faixa} atualizada', antes=antes, depois=snapshot_modelo(faixa))
            messages.success(request, 'Faixa de cobertura atualizada.')
            return redirect('estoque:faixa-cobertura-list')
        return render(request, self.template_name, {'form': form, 'faixa': faixa, 'title': f'Editar faixa de cobertura — {faixa}'})


class FaixaCoberturaDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'excluir'

    def post(self, request, pk):
        empresa = empresa_operacional(request)
        faixa = get_object_or_404(FaixaCoberturaEstoque.objects.filter(empresa=empresa), pk=pk)
        descricao = str(faixa)
        antes = snapshot_modelo(faixa)
        _auditar_faixa(request, 'excluir', faixa, f'Faixa de cobertura {descricao} excluída', antes=antes)
        faixa.delete()
        messages.success(request, 'Faixa de cobertura excluída.')
        return redirect('estoque:faixa-cobertura-list')
