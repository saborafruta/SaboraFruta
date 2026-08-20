from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.cadastros.forms import FuncionarioForm
from apps.cadastros.models import Funcionario
from apps.core.services.permissions import PermissaoRequiredMixin


class FuncionarioListView(PermissaoRequiredMixin, View):
    permissao_modulo = "cadastros"
    permissao_acao = "ver"

    def get(self, request):
        busca = request.GET.get("q", "").strip()
        mostrar_inativos = request.GET.get("inativos") == "1"
        qs = Funcionario.objects.for_filial(request.filial_ativa)
        if not mostrar_inativos:
            qs = qs.filter(ativo=True)
        if busca:
            qs = qs.filter(
                Q(nome__icontains=busca)
                | Q(cpf__icontains="".join(filter(str.isdigit, busca)))
                | Q(cargo__icontains=busca)
                | Q(chave_pix__icontains=busca)
            )
        qs = qs.order_by("nome", "pk")
        page_obj = Paginator(qs, 50).get_page(request.GET.get("page"))
        params = request.GET.copy()
        params.pop("page", None)
        return render(request, "cadastros/funcionario/list.html", {
            "funcionarios": page_obj.object_list,
            "page_obj": page_obj,
            "page_querystring": params.urlencode(),
            "busca": busca,
            "mostrar_inativos": mostrar_inativos,
            "pode_editar": request.user.tem_permissao("cadastros", "editar"),
        })


class FuncionarioCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = "cadastros"
    permissao_acao = "criar"

    def get(self, request):
        return self._render(request, FuncionarioForm(filial=request.filial_ativa))

    def post(self, request):
        form = FuncionarioForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            funcionario = form.save(commit=False)
            funcionario.filial = request.filial_ativa
            funcionario.save()
            messages.success(request, f'Funcionario "{funcionario}" cadastrado.')
            return redirect("cadastros:funcionario-list")
        return self._render(request, form)

    def _render(self, request, form):
        return render(request, "cadastros/funcionario/form.html", {
            "form": form,
            "title": "Novo Funcionario",
        })


class FuncionarioUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = "cadastros"
    permissao_acao = "editar"

    def _get(self, request, pk):
        return get_object_or_404(Funcionario.objects.for_filial(request.filial_ativa), pk=pk)

    def get(self, request, pk):
        funcionario = self._get(request, pk)
        return self._render(request, funcionario, FuncionarioForm(instance=funcionario, filial=request.filial_ativa))

    def post(self, request, pk):
        funcionario = self._get(request, pk)
        form = FuncionarioForm(request.POST, instance=funcionario, filial=request.filial_ativa)
        if form.is_valid():
            form.save()
            messages.success(request, "Funcionario atualizado.")
            return redirect("cadastros:funcionario-list")
        return self._render(request, funcionario, form)

    def _render(self, request, funcionario, form):
        return render(request, "cadastros/funcionario/form.html", {
            "form": form,
            "funcionario": funcionario,
            "title": f"Editar - {funcionario}",
        })


class FuncionarioToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = "cadastros"
    permissao_acao = "editar"

    def post(self, request, pk):
        funcionario = get_object_or_404(Funcionario.objects.for_filial(request.filial_ativa), pk=pk)
        funcionario.ativo = not funcionario.ativo
        funcionario.save(update_fields=["ativo", "updated_at"])
        messages.success(request, f'Funcionario "{funcionario}" {"ativado" if funcionario.ativo else "desativado"}.')
        return redirect("cadastros:funcionario-list")
