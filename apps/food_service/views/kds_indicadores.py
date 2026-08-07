"""Indicadores de tempo de preparo -- relatório histórico (não é tela de polling)."""
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin

from ..services import KdsIndicadoresService


class KdsIndicadoresView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        hoje = timezone.localdate()
        data_inicio = parse_date(request.GET.get('data_inicio', '')) or hoje
        data_fim = parse_date(request.GET.get('data_fim', '')) or hoje

        resumo = KdsIndicadoresService.resumo(request.filial_ativa, data_inicio, data_fim)

        return render(request, 'food_service/kds_indicadores.html', {
            'title': 'Indicadores de Preparo',
            'resumo': resumo,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
        })
