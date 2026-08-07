"""Relatorios Gerenciais -- dashboard unico com filtros de periodo/turno/
colaborador, cobrindo Vendas/Produtos/Atendimento/Operacao/Financeiro."""
from __future__ import annotations

from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.core.models import Usuario
from apps.food_service.services.relatorios_service import RelatoriosGerenciaisService, TURNOS


class RelatoriosGerenciaisView(PermissaoRequiredMixin, View):
    permissao_modulo = 'food_service'
    permissao_acao = 'ver'

    def get(self, request):
        filial = request.filial_ativa
        hoje = timezone.localdate()
        data_inicio = parse_date(request.GET.get('data_inicio', '')) or hoje.replace(day=1)
        data_fim = parse_date(request.GET.get('data_fim', '')) or hoje
        turno = request.GET.get('turno', '') or None
        colaborador_raw = request.GET.get('colaborador', '')
        colaborador_id = int(colaborador_raw) if colaborador_raw.isdigit() else None

        vendas = RelatoriosGerenciaisService.vendas(filial, data_inicio, data_fim, turno, colaborador_id)
        faturamento_diario = RelatoriosGerenciaisService.faturamento_serie(filial, 'dia', 14)
        faturamento_mensal = RelatoriosGerenciaisService.faturamento_serie(filial, 'mes', 12)
        produtos = RelatoriosGerenciaisService.produtos(filial, data_inicio, data_fim, turno)
        atendimento = RelatoriosGerenciaisService.atendimento(filial, data_inicio, data_fim, turno, colaborador_id)
        operacao = RelatoriosGerenciaisService.operacao(filial, data_inicio, data_fim, turno, colaborador_id)
        financeiro = RelatoriosGerenciaisService.financeiro(filial, data_inicio, data_fim)

        colaboradores = Usuario.objects.filter(
            acessos_filiais__filial=filial, acessos_filiais__ativo=True,
        ).distinct().order_by('nome')

        return render(request, 'food_service/relatorios.html', {
            'title': 'Relatórios Gerenciais',
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'turno': turno,
            'turnos': TURNOS,
            'colaborador_id': colaborador_id,
            'colaboradores': colaboradores,
            'vendas': vendas,
            'faturamento_diario': faturamento_diario,
            'faturamento_mensal': faturamento_mensal,
            'produtos': produtos,
            'atendimento': atendimento,
            'operacao': operacao,
            'financeiro': financeiro,
        })
