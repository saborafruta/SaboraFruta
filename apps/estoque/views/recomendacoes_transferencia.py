from django.core.paginator import Paginator
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.motivo_transferencia import enriquecer_para_tabela
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


class RecomendacoesTransferenciaView(PermissaoRequiredMixin, View):
    """Tabela de recomendações de transferência, com explicação por linha (Fases 13-14)."""

    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/recomendacoes_transferencia/list.html"

    def get(self, request):
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)
        busca = (request.GET.get("busca") or "").strip()[:150]
        filial_origem_id = _id_opcional(request.GET.get("origem"))
        filial_destino_id = _id_opcional(request.GET.get("destino"))

        resultado = calcular_equilibrio(
            empresa=request.user.empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura,
            busca=busca, filial_origem_id=filial_origem_id, filial_destino_id=filial_destino_id,
        )
        recomendacoes = [
            enriquecer_para_tabela(sugestao, dias_analise=dias_analise)
            for sugestao in resultado["sugestoes"]
        ]

        pagina = Paginator(recomendacoes, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "title": "Tabela de recomendações",
            "pagina": pagina,
            "total": len(recomendacoes),
            "filiais": resultado["filiais"],
            "busca": busca,
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "filial_origem_id": filial_origem_id,
            "filial_destino_id": filial_destino_id,
            "permissoes_estoque": permissoes_estoque(request),
        })
