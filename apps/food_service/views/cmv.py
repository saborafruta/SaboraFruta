"""Controle de CMV -- relatório histórico (não é tela de polling)."""
from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin

from ..services import CmvService


class CmvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        hoje = timezone.localdate()
        data_inicio = parse_date(request.GET.get('data_inicio', '')) or hoje.replace(day=1)
        data_fim = parse_date(request.GET.get('data_fim', '')) or hoje

        meta_raw = request.GET.get('meta_cmv', '').strip().replace(',', '.')
        try:
            meta_cmv_percentual = Decimal(meta_raw) if meta_raw else None
        except InvalidOperation:
            meta_cmv_percentual = None

        resumo = CmvService.resumo(request.filial_ativa, data_inicio, data_fim, meta_cmv_percentual)

        return render(request, 'food_service/cmv.html', {
            'title': 'Controle de CMV',
            'resumo': resumo,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'meta_cmv_percentual': meta_cmv_percentual,
        })
