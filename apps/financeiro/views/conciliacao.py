"""Conciliacao bancaria -- lancamentos de extrato x baixas de contas a
receber/pagar. Sem importador automatico (OFX/CNAB) ainda -- lancamento
manual do extrato por enquanto, o casamento com as contas e' o que importa
aqui."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.models.conta_bancaria import ContaBancaria
from apps.financeiro.models.extrato import ExtratoBancario
from apps.financeiro.services.conciliacao_service import ConciliacaoService


class ExtratoListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'
    template_name = 'financeiro/conciliacao/list.html'

    def get(self, request):
        filial = request.filial_ativa
        qs = ExtratoBancario.objects.filter(filial=filial).select_related('conta_bancaria')

        status = request.GET.get('status', '')
        if status:
            qs = qs.filter(status=status)
        conta_bancaria_id = request.GET.get('conta_bancaria', '')
        if conta_bancaria_id:
            qs = qs.filter(conta_bancaria_id=conta_bancaria_id)

        page_obj = Paginator(qs, 50).get_page(request.GET.get('page'))

        linhas = []
        for extrato in page_obj.object_list:
            linhas.append({
                'extrato': extrato,
                'sugestoes': ConciliacaoService.sugestoes(extrato) if extrato.status != 'conciliado' else [],
            })

        return render(request, self.template_name, {
            'title': 'Conciliação Bancária',
            'page_obj': page_obj,
            'linhas': linhas,
            'status': status,
            'conta_bancaria_id': conta_bancaria_id,
            'contas_bancarias': ContaBancaria.objects.filter(filial=filial, ativo=True),
            'total_pendentes': qs.filter(status='importado').count(),
            'total_conciliados': qs.filter(status='conciliado').count(),
        })


class ExtratoLancarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def post(self, request):
        filial = request.filial_ativa
        conta_bancaria = get_object_or_404(ContaBancaria, pk=request.POST.get('conta_bancaria'), filial=filial)
        data_lancamento = parse_date(request.POST.get('data_lancamento', ''))
        historico = request.POST.get('historico', '').strip()
        valor_raw = request.POST.get('valor', '').strip().replace(',', '.')

        if not data_lancamento:
            messages.error(request, 'Informe a data do lançamento.')
            return redirect(reverse('financeiro:conciliacao_list'))
        try:
            valor = Decimal(valor_raw)
        except InvalidOperation:
            messages.error(request, 'Valor inválido. Use positivo para crédito e negativo para débito.')
            return redirect(reverse('financeiro:conciliacao_list'))

        ExtratoBancario.objects.create(
            conta_bancaria=conta_bancaria,
            filial=filial,
            data_lancamento=data_lancamento,
            historico=historico,
            valor=valor,
            origem='manual',
            status='importado',
        )
        messages.success(request, 'Lançamento adicionado ao extrato.')
        return redirect(reverse('financeiro:conciliacao_list'))


class ExtratoConciliarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        filial = request.filial_ativa
        extrato = get_object_or_404(ExtratoBancario, pk=pk, filial=filial)
        lancamento_tipo = request.POST.get('lancamento_tipo', '')
        lancamento_id = request.POST.get('lancamento_id', '')
        try:
            ConciliacaoService.conciliar(extrato, lancamento_tipo, int(lancamento_id), request.user)
            messages.success(request, 'Lançamento conciliado.')
        except (DomainError, ValueError, TypeError) as exc:
            messages.error(request, str(exc) or 'Não foi possível conciliar.')
        return redirect(reverse('financeiro:conciliacao_list'))


class ExtratoDesconciliarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        filial = request.filial_ativa
        extrato = get_object_or_404(ExtratoBancario, pk=pk, filial=filial)
        conciliacao = extrato.conciliacoes.first()
        if conciliacao:
            ConciliacaoService.desconciliar(conciliacao)
            messages.success(request, 'Conciliação desfeita.')
        return redirect(reverse('financeiro:conciliacao_list'))
