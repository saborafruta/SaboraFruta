"""Fases 27-28: relatórios e indicadores de performance da equalização."""
from django.shortcuts import render
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.services.request_scope import empresa_operacional
from apps.estoque.services.indicadores_performance import calcular_indicadores
from apps.estoque.services.relatorios_equalizacao import (
    relatorio_produtos_mais_transferidos, relatorio_produtos_parados,
    relatorio_ruptura, relatorio_transferencias_por_filial,
)
from apps.estoque.views.permissoes import permissoes_estoque


def _inteiro_opcao(valor, permitidos, padrao):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    return numero if numero in permitidos else padrao


class RelatoriosEqualizacaoView(PermissaoRequiredMixin, View):
    permissao_modulo = "estoque"
    permissao_acao = "ver"
    template_name = "estoque/relatorios_equalizacao/central.html"

    def get(self, request):
        empresa = empresa_operacional(request)
        dias_analise = _inteiro_opcao(request.GET.get("dias_analise"), {7, 15, 30, 60, 90}, 30)
        dias_cobertura = _inteiro_opcao(request.GET.get("dias_cobertura"), {7, 14, 21, 30, 45}, 14)
        dias_historico = _inteiro_opcao(request.GET.get("dias_historico"), {7, 15, 30, 60, 90}, 30)

        indicadores = calcular_indicadores(
            empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura, dias_historico=dias_historico,
        )
        return render(request, self.template_name, {
            "title": "Central de relatórios e indicadores",
            "dias_analise": dias_analise,
            "dias_cobertura": dias_cobertura,
            "dias_historico": dias_historico,
            "indicadores": indicadores["atuais"],
            "historico": indicadores["historico"],
            "ruptura": relatorio_ruptura(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)[:20],
            "parados": relatorio_produtos_parados(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)[:20],
            "por_filial": relatorio_transferencias_por_filial(empresa=empresa, dias=dias_historico),
            "mais_transferidos": relatorio_produtos_mais_transferidos(empresa=empresa, dias=dias_historico),
            "permissoes_estoque": permissoes_estoque(request),
        })
