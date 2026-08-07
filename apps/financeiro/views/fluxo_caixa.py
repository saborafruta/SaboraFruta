"""Fluxo de Caixa -- relatorio (realizado + projetado)."""
from __future__ import annotations

from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.services.fluxo_caixa_service import FluxoCaixaService


class FluxoCaixaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        hoje = timezone.localdate()
        data_inicio = parse_date(request.GET.get('data_inicio', '')) or hoje.replace(day=1)
        data_fim = parse_date(request.GET.get('data_fim', '')) or hoje

        resumo = FluxoCaixaService.apurar(request.filial_ativa, data_inicio, data_fim)

        return render(request, 'financeiro/fluxo_caixa.html', {
            'title': 'Fluxo de Caixa',
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'resumo': resumo,
        })
