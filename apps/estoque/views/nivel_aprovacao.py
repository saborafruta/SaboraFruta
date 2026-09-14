"""CRUD das faixas de aprovação (referência documental -- ver apps/estoque/services/aprovacao_transferencia.py)."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.forms import NivelAprovacaoTransferenciaForm
from apps.estoque.models import NivelAprovacaoTransferencia
from apps.estoque.views.permissoes import permissoes_estoque


class NivelAprovacaoListView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/nivel_aprovacao/list.html"

    def get(self, request):
        niveis = NivelAprovacaoTransferencia.objects.filter(empresa=empresa_operacional(request)).order_by("valor_minimo")
        return render(request, self.template_name, {
            "title": "Faixas de aprovação",
            "niveis": niveis,
            "permissoes_estoque": permissoes_estoque(request),
        })


class NivelAprovacaoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "criar"
    template_name = "estoque/nivel_aprovacao/form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": NivelAprovacaoTransferenciaForm(), "title": "Nova faixa de aprovação"})

    def post(self, request):
        form = NivelAprovacaoTransferenciaForm(request.POST)
        if form.is_valid():
            nivel = form.save(commit=False)
            nivel.empresa = empresa_operacional(request)
            nivel.save()
            messages.success(request, "Faixa de aprovação criada.")
            return redirect("estoque:nivel-aprovacao-list")
        return render(request, self.template_name, {"form": form, "title": "Nova faixa de aprovação"})


class NivelAprovacaoUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "editar"
    template_name = "estoque/nivel_aprovacao/form.html"

    def _get(self, request, pk):
        return get_object_or_404(NivelAprovacaoTransferencia.objects.filter(empresa=empresa_operacional(request)), pk=pk)

    def get(self, request, pk):
        nivel = self._get(request, pk)
        return render(request, self.template_name, {"form": NivelAprovacaoTransferenciaForm(instance=nivel), "nivel": nivel, "title": f"Editar {nivel.nivel_nome}"})

    def post(self, request, pk):
        nivel = self._get(request, pk)
        form = NivelAprovacaoTransferenciaForm(request.POST, instance=nivel)
        if form.is_valid():
            form.save()
            messages.success(request, "Faixa de aprovação atualizada.")
            return redirect("estoque:nivel-aprovacao-list")
        return render(request, self.template_name, {"form": form, "nivel": nivel, "title": f"Editar {nivel.nivel_nome}"})


class NivelAprovacaoDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "excluir"

    def post(self, request, pk):
        nivel = get_object_or_404(NivelAprovacaoTransferencia.objects.filter(empresa=empresa_operacional(request)), pk=pk)
        nivel.delete()
        messages.success(request, "Faixa de aprovação excluída.")
        return redirect("estoque:nivel-aprovacao-list")
