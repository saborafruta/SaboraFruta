from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.services.equilibrio_estoque import calcular_equilibrio
from apps.estoque.services.mapa_estoque import detalhe_filial, montar_mapa
from apps.estoque.views.permissoes import permissoes_estoque


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


class MapaEstoqueView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/mapa_estoque/mapa.html"

    def get(self, request):
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)
        empresa = empresa_operacional(request)
        mapa = montar_mapa(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)

        filial_selecionada_id = request.GET.get("filial")
        detalhe = None
        transferencias_recomendadas = []
        if filial_selecionada_id:
            try:
                filial_selecionada_id = int(filial_selecionada_id)
            except (TypeError, ValueError):
                filial_selecionada_id = None
        if filial_selecionada_id:
            detalhe = detalhe_filial(
                empresa=empresa, filial_id=filial_selecionada_id,
                dias_analise=dias_analise, dias_cobertura=dias_cobertura,
            )
            resultado = calcular_equilibrio(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
            transferencias_recomendadas = [
                item for item in resultado["sugestoes"]
                if item["origem"].pk == filial_selecionada_id or item["destino"].pk == filial_selecionada_id
            ]

        return render(request, self.template_name, {
            "title": "Mapa de estoque",
            "mapa": mapa,
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "filial_selecionada_id": filial_selecionada_id,
            "detalhe": detalhe,
            "transferencias_recomendadas": transferencias_recomendadas,
            "permissoes_estoque": permissoes_estoque(request),
        })
