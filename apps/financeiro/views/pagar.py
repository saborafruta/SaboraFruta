"""Views de Contas a Pagar."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.constants.enums import StatusContaPagar
from apps.financeiro.forms.pagar import ContaPagarForm, PagamentoContaPagarForm
from apps.financeiro.models.receber_pagar import ContaPagar
from apps.financeiro.services.pagar_service import ContaPagarService

STATUS_CHOICES = StatusContaPagar.choices

PILL_STATUS = {
    StatusContaPagar.ABERTO:    'is-blue',
    StatusContaPagar.PAGO:      'is-green',
    StatusContaPagar.VENCIDO:   'is-red',
    StatusContaPagar.CANCELADO: 'is-slate',
    StatusContaPagar.AGENDADO:  'is-amber',
}


def _filial(request):
    return request.filial_ativa


def _kpis(qs_base):
    hoje = timezone.localdate()
    primeiro_dia_mes = hoje.replace(day=1)

    totais = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO, StatusContaPagar.AGENDADO]
    ).aggregate(
        total_aberto=Sum('valor_saldo'),
        qtd_aberto=Count('id'),
    )

    vencido = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO],
        data_vencimento__lt=hoje,
    ).aggregate(total_vencido=Sum('valor_saldo'))

    pago_mes = qs_base.filter(
        status=StatusContaPagar.PAGO,
        data_pagamento__gte=primeiro_dia_mes,
    ).aggregate(total_mes=Sum('valor_pago'))

    vence_hoje = qs_base.filter(
        status__in=[StatusContaPagar.ABERTO, StatusContaPagar.VENCIDO],
        data_vencimento=hoje,
    ).aggregate(total_hoje=Sum('valor_saldo'), qtd_hoje=Count('id'))

    return {
        'kpi_total_aberto':  totais['total_aberto']    or 0,
        'kpi_qtd_aberto':    totais['qtd_aberto']      or 0,
        'kpi_total_vencido': vencido['total_vencido']  or 0,
        'kpi_total_mes':     pago_mes['total_mes']     or 0,
        'kpi_total_hoje':    vence_hoje['total_hoje']  or 0,
        'kpi_qtd_hoje':      vence_hoje['qtd_hoje']    or 0,
    }


class ContaPagarListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = _filial(request)
        ContaPagarService.atualizar_status_vencidos(filial)

        qs = (
            ContaPagar.objects.for_filial(filial)
            .select_related('fornecedor', 'funcionario', 'forma_pagamento')
            .order_by('data_vencimento')
        )

        kpis = _kpis(qs)

        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')

        if status:
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(fornecedor__razao_social__icontains=q)
                | Q(fornecedor__nome_fantasia__icontains=q)
                | Q(funcionario__nome__icontains=q)
                | Q(funcionario__cpf__icontains=q)
                | Q(documento_numero__icontains=q)
                | Q(nota_fiscal_fornecedor__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)

        totais_filtro = qs.aggregate(
            total_valor=Sum('valor_final'),
            total_saldo=Sum('valor_saldo'),
            total_pago=Sum('valor_pago'),
        )

        paginator = Paginator(qs, 50)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        qd = request.GET.copy()
        qd.pop('page', None)
        page_querystring = qd.urlencode()

        pode_criar = request.user.tem_permissao('financeiro', 'criar')
        pode_editar = request.user.tem_permissao('financeiro', 'editar')

        return render(request, 'financeiro/pagar/list.html', {
            'title': 'Contas a Pagar',
            'page_obj': page_obj,
            'contas': page_obj,
            'status_choices': STATUS_CHOICES,
            'status_filtro': status,
            'q': q,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'totais_filtro': totais_filtro,
            'page_querystring': page_querystring,
            'pill_status': PILL_STATUS,
            'pode_criar': pode_criar,
            'pode_editar': pode_editar,
            'today': timezone.localdate(),
            **kpis,
        })


class ContaPagarRelatorioView(PermissaoRequiredMixin, View):
    """Relatório imprimível: agrupa os títulos (por padrão em aberto) por
    fornecedor, com a nota de entrada vinculada de cada título."""

    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        from apps.compras.models import EntradaNF

        filial = _filial(request)

        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')

        qs = (
            ContaPagar.objects.for_filial(filial)
            .select_related('fornecedor', 'funcionario')
            .order_by('data_vencimento')
        )
        if status:
            qs = qs.filter(status=status)
        else:
            # Sem status escolhido, o relatório foca nos títulos em aberto.
            qs = qs.filter(status__in=[
                StatusContaPagar.ABERTO,
                StatusContaPagar.VENCIDO,
                StatusContaPagar.AGENDADO,
            ])
        if q:
            qs = qs.filter(
                Q(fornecedor__razao_social__icontains=q)
                | Q(funcionario__nome__icontains=q)
                | Q(documento_numero__icontains=q)
                | Q(nota_fiscal_fornecedor__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)

        titulos = list(qs)

        # Carrega as notas de entrada vinculadas (documento_tipo="entrada_nf")
        # só para exibir número/data da compra no cabeçalho de cada título.
        entrada_ids = [
            t.documento_id for t in titulos
            if t.documento_tipo == 'entrada_nf' and t.documento_id
        ]
        entradas = {}
        if entrada_ids:
            entradas = {
                e.pk: e for e in EntradaNF.objects.filter(pk__in=entrada_ids)
            }

        grupos: dict = {}
        for t in titulos:
            chave = (t.tipo_lancamento, t.funcionario_id or t.fornecedor_id or 0)
            g = grupos.get(chave)
            if g is None:
                g = {
                    'fornecedor': t.fornecedor,
                    'beneficiario_nome': t.beneficiario_nome,
                    'beneficiario_documento': t.beneficiario_documento,
                    'titulos': [],
                    'total_saldo': Decimal('0'),
                    'total_valor': Decimal('0'),
                }
                grupos[chave] = g

            entrada = entradas.get(t.documento_id) if t.documento_tipo == 'entrada_nf' else None
            g['titulos'].append({'titulo': t, 'entrada': entrada})
            g['total_saldo'] += t.valor_saldo or Decimal('0')
            g['total_valor'] += t.valor_final or Decimal('0')

        fornecedores = sorted(
            grupos.values(),
            key=lambda x: x['beneficiario_nome'].lower(),
        )

        total_geral_saldo = sum((g['total_saldo'] for g in fornecedores), Decimal('0'))
        total_geral_valor = sum((g['total_valor'] for g in fornecedores), Decimal('0'))

        status_label = dict(STATUS_CHOICES).get(status) if status else 'Em aberto'

        return render(request, 'financeiro/pagar/relatorio.html', {
            'title': 'Relatório de Contas a Pagar',
            'fornecedores': fornecedores,
            'filial': filial,
            'q': q,
            'status_label': status_label,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'total_geral_saldo': total_geral_saldo,
            'total_geral_valor': total_geral_valor,
            'total_titulos': len(titulos),
            'gerado_em': timezone.localtime(),
        })


class ContaPagarCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def get(self, request):
        filial = _filial(request)
        form = ContaPagarForm(filial=filial)
        return render(request, 'financeiro/pagar/form.html', {
            'title': 'Nova Conta a Pagar',
            'form': form,
            'cancel_url': reverse('financeiro:pagar_list'),
        })

    def post(self, request):
        filial = _filial(request)
        form = ContaPagarForm(request.POST, filial=filial)
        if not form.is_valid():
            return render(request, 'financeiro/pagar/form.html', {
                'title': 'Nova Conta a Pagar',
                'form': form,
                'cancel_url': reverse('financeiro:pagar_list'),
            })

        d = form.cleaned_data
        try:
            dados_conta = dict(
                filial=filial,
                fornecedor=d.get('fornecedor'),
                funcionario=d.get('funcionario'),
                tipo_lancamento=d['tipo_lancamento'],
                valor_original=d['valor_original'],
                data_emissao=d['data_emissao'],
                documento_numero=d.get('documento_numero', ''),
                nota_fiscal_fornecedor=d.get('nota_fiscal_fornecedor', ''),
                forma_pagamento=d.get('forma_pagamento'),
                plano_contas=d.get('plano_contas'),
                observacao=d.get('observacao', ''),
                usuario=request.user,
            )
            if d['recorrente']:
                contas = ContaPagarService.criar_recorrencia(
                    **dados_conta,
                    quantidade=d['quantidade_recorrencias'],
                    frequencia=d['frequencia_recorrencia'],
                    data_vencimento=d['data_vencimento'],
                    data_competencia=d.get('data_competencia'),
                )
                messages.success(request, f'{len(contas)} títulos recorrentes lançados com sucesso.')
            else:
                conta = ContaPagarService.criar(
                    **dados_conta,
                    data_vencimento=d['data_vencimento'],
                    data_competencia=d.get('data_competencia'),
                    parcela=d['parcela'],
                    total_parcelas=d['total_parcelas'],
                )
                messages.success(request, f'Conta a pagar #{conta.pk} lançada com sucesso.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/pagar/form.html', {
                'title': 'Nova Conta a Pagar',
                'form': form,
                'cancel_url': reverse('financeiro:pagar_list'),
            })

        return redirect(reverse('financeiro:pagar_list'))


class ContaPagarDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request, pk):
        filial = _filial(request)
        conta = get_object_or_404(
            ContaPagar.objects.for_filial(filial).select_related(
                'fornecedor', 'funcionario', 'forma_pagamento', 'conta_bancaria',
                'plano_contas', 'conta_contabil', 'usuario', 'usuario_pagamento',
            ),
            pk=pk,
        )
        pode_pagar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]
        )
        pode_cancelar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaPagar.CANCELADO, StatusContaPagar.PAGO]
        )

        return render(request, 'financeiro/pagar/detail.html', {
            'title': f'Conta a Pagar #{conta.pk}',
            'conta': conta,
            'pode_pagar': pode_pagar,
            'pode_cancelar': pode_cancelar,
            'pill': PILL_STATUS.get(conta.status, 'is-slate'),
        })


class ContaPagarPagamentoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _get_conta(self, request, pk):
        return get_object_or_404(
            ContaPagar.objects.for_filial(_filial(request)).select_related('fornecedor', 'funcionario'),
            pk=pk,
        )

    def get(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(filial=_filial(request), conta=conta)
        return render(request, 'financeiro/pagar/pagamento.html', {
            'title': f'Pagar — #{conta.pk}',
            'conta': conta,
            'form': form,
            'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
        })

    def post(self, request, pk):
        conta = self._get_conta(request, pk)
        if conta.status in [StatusContaPagar.PAGO, StatusContaPagar.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser paga.')
            return redirect(reverse('financeiro:pagar_detail', args=[pk]))

        form = PagamentoContaPagarForm(request.POST, filial=_filial(request), conta=conta)
        if not form.is_valid():
            return render(request, 'financeiro/pagar/pagamento.html', {
                'title': f'Pagar — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
            })

        d = form.cleaned_data
        try:
            ContaPagarService.registrar_pagamento(
                conta=conta,
                data_pagamento=d['data_pagamento'],
                valor_pago=d['valor_pago'],
                forma_pagamento=d['forma_pagamento'],
                usuario=request.user,
                conta_bancaria=d.get('conta_bancaria'),
                valor_juros=d.get('valor_juros'),
                valor_multa=d.get('valor_multa'),
                valor_desconto=d.get('valor_desconto'),
                comprovante_url=d.get('comprovante_url', ''),
                observacao=d.get('observacao', ''),
            )
            if conta.status == StatusContaPagar.PAGO:
                messages.success(request, f'Conta #{pk} paga integralmente. ✓')
            else:
                messages.success(request, f'Pagamento parcial registrado. Saldo restante: R$ {conta.valor_saldo:,.2f}.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/pagar/pagamento.html', {
                'title': f'Pagar — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:pagar_detail', args=[pk]),
            })

        return redirect(reverse('financeiro:pagar_detail', args=[pk]))


class ContaPagarCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        conta = get_object_or_404(
            ContaPagar.objects.for_filial(_filial(request)), pk=pk
        )
        motivo = request.POST.get('motivo', '').strip() or 'Cancelado pelo usuário.'
        try:
            ContaPagarService.cancelar(conta, motivo, request.user)
            messages.success(request, f'Conta #{pk} cancelada.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('financeiro:pagar_detail', args=[pk]))
