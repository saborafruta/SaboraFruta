from django.core.paginator import Paginator
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.views.permissoes import permissoes_estoque


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


def _id_opcional(valor):
    try:
        numero = int(valor)
        return numero if numero > 0 else None
    except (TypeError, ValueError):
        return None


class EquilibrioEstoqueView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/equilibrio/list.html"

    def get(self, request):
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)
        busca = (request.GET.get("busca") or "").strip()[:150]
        filial_origem_id = _id_opcional(request.GET.get("origem"))
        filial_destino_id = _id_opcional(request.GET.get("destino"))
        resultado = calcular_equilibrio(
            empresa=empresa_operacional(request),
            dias_analise=dias_analise,
            dias_cobertura=dias_cobertura,
            busca=busca,
            filial_origem_id=filial_origem_id,
            filial_destino_id=filial_destino_id,
        )
        sugestoes = resultado["sugestoes"]
        pagina = Paginator(sugestoes, 50).get_page(request.GET.get("page"))
        contexto = {
            "title": "Equilíbrio de estoque",
            "pagina": pagina,
            "filiais": resultado["filiais"],
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "busca": busca,
            "filial_origem_id": filial_origem_id,
            "filial_destino_id": filial_destino_id,
            "produtos_analisados": resultado["produtos_analisados"],
            "total_sugestoes": len(sugestoes),
            "total_quantidade": sum((item["quantidade"] for item in sugestoes), 0),
            "filiais_criticas": len({item["destino"].pk for item in sugestoes}),
            "filial_ativa_id": getattr(request, "filial_ativa", None).pk if getattr(request, "filial_ativa", None) else None,
            "permissoes_estoque": permissoes_estoque(request),
        }
        return render(request, self.template_name, contexto)
