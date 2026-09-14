from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.services.dashboard_equalizacao import montar_dashboard
from apps.estoque.views.permissoes import permissoes_estoque


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


class DashboardEqualizacaoView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/dashboard_equalizacao/dashboard.html"

    def get(self, request):
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)
        cards = montar_dashboard(
            empresa=empresa_operacional(request),
            dias_analise=dias_analise,
            dias_cobertura=dias_cobertura,
        )
        return render(request, self.template_name, {
            "title": "Dashboard de equalização",
            "cards": cards,
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "permissoes_estoque": permissoes_estoque(request),
        })
