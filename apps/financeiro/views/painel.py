"""Painel de Gestao Financeira -- ponto unico que reune o que ja vem de
PDV/Caixa (sessao aberta, vendas do dia) e do proprio Financeiro
(receitas/despesas do mes, contas vencendo, resumo do fluxo de caixa),
satisfazendo a integracao automatica pedida no item 13 sem duplicar nada
que ja existe em cada modulo -- so consolida."""
from __future__ import annotations

from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber
from apps.financeiro.services.fluxo_caixa_service import FluxoCaixaService
from apps.pdv.models.sessao import SessaoPDV


class PainelFinanceiroView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = request.filial_ativa
        hoje = timezone.localdate()
        inicio_mes = hoje.replace(day=1)

        fluxo = FluxoCaixaService.apurar(filial, inicio_mes, hoje)

        receber_pendente = ContaReceber.objects.filter(
            filial=filial, status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
        ).aggregate(total=Sum('valor_saldo'))['total'] or 0
        receber_vencido = ContaReceber.objects.filter(
            filial=filial, status=StatusContaReceber.VENCIDO,
        ).aggregate(total=Sum('valor_saldo'))['total'] or 0

        pagar_pendente = ContaPagar.objects.filter(
            filial=filial, status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO, StatusContaPagar.AGENDADO],
        ).aggregate(total=Sum('valor_saldo'))['total'] or 0
        pagar_vencido = ContaPagar.objects.filter(
            filial=filial, status=StatusContaPagar.VENCIDO,
        ).aggregate(total=Sum('valor_saldo'))['total'] or 0

        sessoes_abertas = SessaoPDV.objects.filter(filial=filial, status='aberto').select_related('caixa', 'usuario')

        extrato_pendente = ExtratoBancario.objects.filter(filial=filial, status='importado').count()

        return render(request, 'financeiro/painel.html', {
            'title': 'Gestão Financeira',
            'hoje': hoje,
            'inicio_mes': inicio_mes,
            'fluxo': fluxo,
            'receber_pendente': receber_pendente,
            'receber_vencido': receber_vencido,
            'pagar_pendente': pagar_pendente,
            'pagar_vencido': pagar_vencido,
            'sessoes_abertas': sessoes_abertas,
            'extrato_pendente': extrato_pendente,
        })
