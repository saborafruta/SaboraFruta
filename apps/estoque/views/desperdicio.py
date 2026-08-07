"""Controle de Validade e Desperdicio -- apuracao de perdas e indicadores
de reducao (relatorio historico, complementa apps.estoque.views.alerta,
que cobre os alertas em tempo real de vencimento/minimo/parado/sem giro)."""
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.services.desperdicio_service import DesperdicioService


class DesperdicioDashboardView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    permissao_acao = 'ver'

    def get(self, request):
        hoje = timezone.localdate()
        data_inicio = parse_date(request.GET.get('data_inicio', '')) or hoje.replace(day=1)
        data_fim = parse_date(request.GET.get('data_fim', '')) or hoje

        filial = request.filial_ativa
        resumo = DesperdicioService.resumo_perdas(filial, data_inicio, data_fim)
        por_categoria = DesperdicioService.por_categoria(filial, data_inicio, data_fim)
        evolucao = DesperdicioService.evolucao_mensal(filial, meses=6)
        indicador = DesperdicioService.indicador_reducao(filial)
        parados = DesperdicioService.produtos_parados(filial)
        sem_giro = DesperdicioService.produtos_sem_giro(filial)

        return render(request, 'estoque/desperdicio/dashboard.html', {
            'title': 'Controle de Validade e Desperdício',
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'resumo': resumo,
            'por_categoria': por_categoria,
            'evolucao': evolucao,
            'indicador': indicador,
            'total_parados': parados.count(),
            'total_sem_giro': sem_giro.count(),
        })
