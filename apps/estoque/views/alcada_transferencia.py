"""Configura a alçada de transferência (R$) de cada perfil de acesso da empresa."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from apps.core.models import PerfilAcesso
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.views.permissoes import permissoes_estoque


class AlcadaTransferenciaView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/aprovacao_transferencia/alcadas.html"

    def get(self, request):
        perfis = PerfilAcesso.objects.filter(empresa=empresa_operacional(request), ativo=True).order_by("nome")
        return render(request, self.template_name, {
            "title": "Alçada de transferência por perfil",
            "perfis": perfis,
            "permissoes_estoque": permissoes_estoque(request),
        })

    def post(self, request):
        if not request.user.tem_permissao("estoque", "editar"):
            messages.error(request, "Você não tem permissão para esta ação.")
            return redirect("estoque:alcada-transferencia")

        perfis = PerfilAcesso.objects.filter(empresa=empresa_operacional(request), ativo=True)
        for perfil in perfis:
            bruto = (request.POST.get(f"alcada_{perfil.pk}") or "").strip()
            if not bruto:
                perfil.alcada_transferencia = None
            else:
                try:
                    perfil.alcada_transferencia = Decimal(bruto.replace(",", "."))
                except InvalidOperation:
                    messages.error(request, f'Valor inválido para "{perfil.nome}".')
                    return redirect("estoque:alcada-transferencia")
            perfil.save(update_fields=["alcada_transferencia"])
        messages.success(request, "Alçadas atualizadas.")
        return redirect("estoque:alcada-transferencia")
