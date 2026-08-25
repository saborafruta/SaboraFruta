"""Views de Contas a Receber."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.forms.receber import BaixaContaReceberForm, ContaReceberForm
from apps.financeiro.models.receber_pagar import ContaReceber, PagamentoContaReceber
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.services.dashboard_contas_service import DashboardContasService

STATUS_CHOICES = StatusContaReceber.choices

PILL_STATUS = {
    StatusContaReceber.ABERTO:     'is-blue',
    StatusContaReceber.PAGO_PARCIAL: 'is-amber',
    StatusContaReceber.PAGO:       'is-green',
    StatusContaReceber.VENCIDO:    'is-red',
    StatusContaReceber.CANCELADO:  'is-slate',
    StatusContaReceber.NEGOCIADO:  'is-amber',
    StatusContaReceber.DEVOLVIDO:  'is-purple',
}


def _filial(request):
    return request.filial_ativa


def _kpis(qs_base):
    hoje = timezone.localdate()
    primeiro_dia_mes = hoje.replace(day=1)

    totais = qs_base.filter(
        status__in=[
            StatusContaReceber.ABERTO,
            StatusContaReceber.PAGO_PARCIAL,
            StatusContaReceber.VENCIDO,
            StatusContaReceber.NEGOCIADO,
        ]
    ).aggregate(
        total_aberto=Sum('valor_saldo'),
        qtd_aberto=Count('id'),
    )

    vencido = qs_base.filter(
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.PAGO_PARCIAL, StatusContaReceber.VENCIDO],
        data_vencimento__lt=hoje,
    ).aggregate(total_vencido=Sum('valor_saldo'))

    recebido_mes = qs_base.filter(
        status=StatusContaReceber.PAGO,
        data_pagamento__gte=primeiro_dia_mes,
    ).aggregate(total_mes=Sum('valor_pago'))

    vence_hoje = qs_base.filter(
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.PAGO_PARCIAL, StatusContaReceber.VENCIDO],
        data_vencimento=hoje,
    ).aggregate(total_hoje=Sum('valor_saldo'), qtd_hoje=Count('id'))

    return {
        'kpi_total_aberto':   totais['total_aberto']   or 0,
        'kpi_qtd_aberto':     totais['qtd_aberto']     or 0,
        'kpi_total_vencido':  vencido['total_vencido'] or 0,
        'kpi_total_mes':      recebido_mes['total_mes'] or 0,
        'kpi_total_hoje':     vence_hoje['total_hoje'] or 0,
        'kpi_qtd_hoje':       vence_hoje['qtd_hoje']   or 0,
    }


class ContaReceberListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = _filial(request)
        # Atualiza vencidos silenciosamente
        ContaReceberService.atualizar_status_vencidos(filial)

        qs = (
            ContaReceber.objects.for_filial(filial)
            .select_related('cliente', 'forma_pagamento', 'conta_bancaria', 'plano_contas')
            .order_by('data_vencimento', 'cliente__razao_social')
        )

        kpis = _kpis(qs)

        # Filtros
        status = request.GET.get('status', 'pendentes')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')

        if status == 'pendentes':
            qs = qs.filter(status__in=[
                StatusContaReceber.ABERTO,
                StatusContaReceber.PAGO_PARCIAL,
                StatusContaReceber.VENCIDO,
                StatusContaReceber.NEGOCIADO,
            ])
        elif status and status != 'todos':
            qs = qs.filter(status=status)
        if q:
            qs = qs.filter(
                Q(cliente__razao_social__icontains=q)
                | Q(documento_numero__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)

        # Totais da seleção filtrada
        totais_filtro = qs.aggregate(
            total_valor=Sum('valor_final'),
            total_saldo=Sum('valor_saldo'),
            total_pago=Sum('valor_pago'),
        )

        paginator = Paginator(qs, 50)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        # Querystring sem page para paginação
        qd = request.GET.copy()
        qd.pop('page', None)
        page_querystring = qd.urlencode()

        pode_criar = request.user.tem_permissao('financeiro', 'criar')
        pode_editar = request.user.tem_permissao('financeiro', 'editar')

        return render(request, 'financeiro/receber/list.html', {
            'title': 'Contas a Receber',
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
            'dashboard_contas': DashboardContasService.apurar(filial),
            **kpis,
        })


class ContaReceberRelatorioView(PermissaoRequiredMixin, View):
    """Relatório imprimível: agrupa os títulos (por padrão em aberto) por
    cliente, com a venda vinculada de cada título."""

    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        from apps.pdv.models import VendaPDV

        filial = _filial(request)

        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')

        qs = (
            ContaReceber.objects.for_filial(filial)
            .select_related('cliente')
            .order_by('cliente__razao_social', 'data_vencimento')
        )
        if status:
            qs = qs.filter(status=status)
        else:
            # Sem status escolhido, o relatório foca nos títulos em aberto.
            qs = qs.filter(status__in=[
                StatusContaReceber.ABERTO,
                StatusContaReceber.PAGO_PARCIAL,
                StatusContaReceber.VENCIDO,
                StatusContaReceber.NEGOCIADO,
            ])
        if q:
            qs = qs.filter(
                Q(cliente__razao_social__icontains=q)
                | Q(documento_numero__icontains=q)
            )
        if data_ini:
            qs = qs.filter(data_vencimento__gte=data_ini)
        if data_fim:
            qs = qs.filter(data_vencimento__lte=data_fim)

        titulos = list(qs)

        # Carrega as vendas PDV vinculadas (documento_tipo="venda_pdv") só
        # para exibir número/data da compra no cabeçalho de cada título.
        venda_ids = [
            t.documento_id for t in titulos
            if t.documento_tipo == 'venda_pdv' and t.documento_id
        ]
        vendas = {}
        if venda_ids:
            vendas = {
                v.pk: v for v in VendaPDV.objects.filter(pk__in=venda_ids)
            }

        grupos: dict = {}
        for t in titulos:
            g = grupos.get(t.cliente_id)
            if g is None:
                g = {
                    'cliente': t.cliente,
                    'titulos': [],
                    'total_saldo': Decimal('0'),
                    'total_valor': Decimal('0'),
                }
                grupos[t.cliente_id] = g

            venda = vendas.get(t.documento_id) if t.documento_tipo == 'venda_pdv' else None
            g['titulos'].append({'titulo': t, 'venda': venda})
            g['total_saldo'] += t.valor_saldo or Decimal('0')
            g['total_valor'] += t.valor_final or Decimal('0')

        clientes = sorted(
            grupos.values(),
            key=lambda x: (x['cliente'].razao_social or '').lower(),
        )

        total_geral_saldo = sum((g['total_saldo'] for g in clientes), Decimal('0'))
        total_geral_valor = sum((g['total_valor'] for g in clientes), Decimal('0'))

        status_label = dict(STATUS_CHOICES).get(status) if status else 'Em aberto'

        return render(request, 'financeiro/receber/relatorio.html', {
            'title': 'Relatório de Contas a Receber',
            'clientes': clientes,
            'filial': filial,
            'q': q,
            'status_filtro': status,
            'status_label': status_label,
            'data_ini': data_ini,
            'data_fim': data_fim,
            'total_geral_saldo': total_geral_saldo,
            'total_geral_valor': total_geral_valor,
            'total_titulos': len(titulos),
            'gerado_em': timezone.localtime(),
        })


@method_decorator(xframe_options_sameorigin, name='dispatch')
class ContaReceberCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def _context(self, request, form):
        return {
            'title': 'Nova Conta a Receber',
            'form': form,
            'cancel_url': reverse('financeiro:receber_list'),
            'modal_mode': request.GET.get('modal') == '1',
        }

    def get(self, request):
        filial = _filial(request)
        form = ContaReceberForm(filial=filial)
        return render(request, 'financeiro/receber/form.html', self._context(request, form))

    def post(self, request):
        filial = _filial(request)
        form = ContaReceberForm(request.POST, filial=filial)
        if not form.is_valid():
            return render(request, 'financeiro/receber/form.html', self._context(request, form))

        d = form.cleaned_data
        try:
            conta = ContaReceberService.criar(
                filial=filial,
                cliente=d['cliente'],
                valor_original=d['valor_original'],
                data_emissao=d['data_emissao'],
                data_vencimento=d['data_vencimento'],
                parcela=d['parcela'],
                total_parcelas=d['total_parcelas'],
                documento_numero=d.get('documento_numero', ''),
                forma_pagamento=d.get('forma_pagamento'),
                plano_contas=d.get('plano_contas'),
                observacao=d.get('observacao', ''),
                usuario=request.user,
            )
            if d.get('quitar_ao_lancar'):
                ContaReceberService.registrar_baixa(
                    conta=conta,
                    data_pagamento=d['data_pagamento_inicial'],
                    valor_pago=d['valor_pago_inicial'],
                    forma_pagamento=d['forma_pagamento_utilizada'],
                    usuario=request.user,
                    conta_bancaria=d.get('conta_bancaria_recebimento'),
                    observacao='Recebimento registrado no lançamento manual.',
                )
                messages.success(request, f'Conta a receber #{conta.pk} lançada e recebida com sucesso.')
            else:
                messages.success(request, f'Conta a receber #{conta.pk} lançada com sucesso.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/receber/form.html', self._context(request, form))

        if request.GET.get('modal') == '1':
            return render(request, 'financeiro/receber/modal_success.html')
        return redirect(reverse('financeiro:receber_list'))


class ContaReceberDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request, pk):
        filial = _filial(request)
        conta = get_object_or_404(
            ContaReceber.objects.for_filial(filial).select_related(
                'cliente', 'forma_pagamento', 'conta_bancaria',
                'plano_contas', 'usuario', 'usuario_baixa',
            ).prefetch_related(
                'pagamentos__forma_pagamento', 'pagamentos__conta_bancaria', 'pagamentos__usuario',
            ),
            pk=pk,
        )
        pode_baixar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaReceber.PAGO, StatusContaReceber.CANCELADO]
        )
        pode_cancelar = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status not in [StatusContaReceber.CANCELADO, StatusContaReceber.PAGO]
        )
        pode_editar_prazo = pode_cancelar
        pode_gerenciar_recebimentos = (
            request.user.tem_permissao('financeiro', 'editar')
            and conta.status != StatusContaReceber.CANCELADO
        )
        pill = PILL_STATUS.get(conta.status, 'is-slate')

        prazo_retorno_url = request.META.get('HTTP_REFERER') or request.path
        if not url_has_allowed_host_and_scheme(prazo_retorno_url, allowed_hosts={request.get_host()}):
            prazo_retorno_url = request.path
        if '?modal=1' in prazo_retorno_url:
            prazo_retorno_url = request.path

        context = {
            'title': f'Conta a Receber #{conta.pk}',
            'conta': conta,
            'pode_baixar': pode_baixar,
            'pode_cancelar': pode_cancelar,
            'pode_editar_prazo': pode_editar_prazo,
            'pode_gerenciar_recebimentos': pode_gerenciar_recebimentos,
            'pill': pill,
            'tipo_conta': 'receber',
            'prazo_retorno_url': prazo_retorno_url,
        }
        if request.GET.get('modal') == '1':
            return render(request, 'financeiro/_detalhes_conta_modal.html', context)
        return render(request, 'financeiro/receber/detail.html', context)


class ContaReceberBaixaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _get_conta(self, request, pk):
        filial = _filial(request)
        return get_object_or_404(
            ContaReceber.objects.for_filial(filial).select_related('cliente'),
            pk=pk,
        )

    def _context(self, request, conta, form):
        return {
            'title': f'Receber — #{conta.pk}',
            'conta': conta,
            'form': form,
            'cancel_url': reverse('financeiro:receber_detail', args=[conta.pk]),
            'modal_form': request.GET.get('modal') == '1',
            'form_action_url': f"{reverse('financeiro:receber_baixar', args=[conta.pk])}{'?modal=1' if request.GET.get('modal') == '1' else ''}",
            'editando_pagamento': False,
        }

    def _render_form(self, request, conta, form, status=200):
        template = (
            'financeiro/receber/_baixa_modal.html'
            if request.GET.get('modal') == '1'
            else 'financeiro/receber/baixa.html'
        )
        return render(request, template, self._context(request, conta, form), status=status)

    def get(self, request, pk):
        conta = self._get_conta(request, pk)
        filial = _filial(request)

        if conta.status in [StatusContaReceber.PAGO, StatusContaReceber.CANCELADO]:
            if request.GET.get('modal') == '1':
                return ContaReceberDetailView().get(request, pk)
            messages.warning(request, 'Esta conta não pode ser baixada.')
            return redirect(reverse('financeiro:receber_detail', args=[pk]))

        form = BaixaContaReceberForm(filial=filial, conta=conta)
        return self._render_form(request, conta, form)

    def post(self, request, pk):
        conta = self._get_conta(request, pk)
        filial = _filial(request)

        if conta.status in [StatusContaReceber.PAGO, StatusContaReceber.CANCELADO]:
            if request.GET.get('modal') == '1':
                return ContaReceberDetailView().get(request, pk)
            messages.warning(request, 'Esta conta não pode ser baixada.')
            return redirect(reverse('financeiro:receber_detail', args=[pk]))

        form = BaixaContaReceberForm(request.POST, filial=filial, conta=conta)
        if not form.is_valid():
            return self._render_form(
                request, conta, form,
                status=400 if request.GET.get('modal') == '1' else 200,
            )

        d = form.cleaned_data
        try:
            ContaReceberService.registrar_baixa(
                conta=conta,
                data_pagamento=d['data_pagamento'],
                valor_pago=d['valor_pago'],
                forma_pagamento=d['forma_pagamento'],
                usuario=request.user,
                conta_bancaria=d.get('conta_bancaria'),
                valor_juros=d.get('valor_juros'),
                valor_multa=d.get('valor_multa'),
                valor_desconto=d.get('valor_desconto'),
                observacao=d.get('observacao', ''),
                bandeira=d.get('bandeira', ''),
                numero_parcelas=d.get('numero_parcelas'),
            )
            if conta.status == StatusContaReceber.PAGO:
                messages.success(request, f'Conta #{pk} recebida integralmente. ✓')
            else:
                messages.success(request, f'Baixa parcial registrada. Valor restante: R$ {conta.valor_saldo:,.2f}.')
        except DomainError as exc:
            if request.GET.get('modal') == '1':
                form.add_error(None, str(exc))
                return self._render_form(request, conta, form, status=400)
            messages.error(request, str(exc))
            return self._render_form(request, conta, form)

        if request.GET.get('modal') == '1':
            return ContaReceberDetailView().get(request, pk)
        return redirect(reverse('financeiro:receber_detail', args=[pk]))


class ContaReceberPagamentoEditView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def _get_pagamento(self, request, pk, pagamento_pk):
        filial = _filial(request)
        return get_object_or_404(
            PagamentoContaReceber.objects.for_filial(filial).select_related(
                'conta_receber', 'conta_receber__cliente', 'forma_pagamento', 'conta_bancaria',
            ),
            pk=pagamento_pk,
            conta_receber_id=pk,
        )

    def _context(self, request, pagamento, form):
        conta = pagamento.conta_receber
        return {
            'title': f'Editar recebimento — #{conta.pk}',
            'conta': conta,
            'pagamento': pagamento,
            'form': form,
            'cancel_url': reverse('financeiro:receber_detail', args=[conta.pk]),
            'modal_form': request.GET.get('modal') == '1',
            'form_action_url': (
                f"{reverse('financeiro:receber_pagamento_editar', args=[conta.pk, pagamento.pk])}"
                f"{'?modal=1' if request.GET.get('modal') == '1' else ''}"
            ),
            'editando_pagamento': True,
        }

    def _render_form(self, request, pagamento, form, status=200):
        template = (
            'financeiro/receber/_baixa_modal.html'
            if request.GET.get('modal') == '1'
            else 'financeiro/receber/baixa.html'
        )
        return render(request, template, self._context(request, pagamento, form), status=status)

    def get(self, request, pk, pagamento_pk):
        pagamento = self._get_pagamento(request, pk, pagamento_pk)
        form = BaixaContaReceberForm(
            filial=_filial(request),
            conta=pagamento.conta_receber,
            pagamento=pagamento,
        )
        return self._render_form(request, pagamento, form)

    def post(self, request, pk, pagamento_pk):
        pagamento = self._get_pagamento(request, pk, pagamento_pk)
        conta = pagamento.conta_receber
        form = BaixaContaReceberForm(
            request.POST,
            filial=_filial(request),
            conta=conta,
            pagamento=pagamento,
        )
        if not form.is_valid():
            return self._render_form(
                request, pagamento, form,
                status=400 if request.GET.get('modal') == '1' else 200,
            )
        d = form.cleaned_data
        try:
            ContaReceberService.editar_baixa(
                pagamento=pagamento,
                data_pagamento=d['data_pagamento'],
                valor_pago=d['valor_pago'],
                forma_pagamento=d['forma_pagamento'],
                usuario=request.user,
                conta_bancaria=d.get('conta_bancaria'),
                valor_juros=d.get('valor_juros'),
                valor_multa=d.get('valor_multa'),
                valor_desconto=d.get('valor_desconto'),
                observacao=d.get('observacao', ''),
                bandeira=d.get('bandeira', ''),
                numero_parcelas=d.get('numero_parcelas'),
            )
            messages.success(request, 'Recebimento atualizado.')
        except DomainError as exc:
            if request.GET.get('modal') == '1':
                form.add_error(None, str(exc))
                return self._render_form(request, pagamento, form, status=400)
            messages.error(request, str(exc))
            return self._render_form(request, pagamento, form)

        if request.GET.get('modal') == '1':
            return ContaReceberDetailView().get(request, pk)
        return redirect(reverse('financeiro:receber_detail', args=[pk]))


class ContaReceberPagamentoExcluirView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk, pagamento_pk):
        filial = _filial(request)
        pagamento = get_object_or_404(
            PagamentoContaReceber.objects.for_filial(filial).select_related('conta_receber'),
            pk=pagamento_pk,
            conta_receber_id=pk,
        )
        motivo = request.POST.get('motivo', '').strip() or 'Excluído pelo usuário.'
        try:
            ContaReceberService.excluir_baixa(pagamento, motivo, request.user)
            messages.success(request, 'Recebimento excluído e título recalculado.')
        except DomainError as exc:
            messages.error(request, str(exc))
        if request.GET.get('modal') == '1':
            return ContaReceberDetailView().get(request, pk)
        return redirect(reverse('financeiro:receber_detail', args=[pk]))


class ContaReceberCancelarView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        filial = _filial(request)
        conta = get_object_or_404(
            ContaReceber.objects.for_filial(filial), pk=pk
        )
        motivo = request.POST.get('motivo', '').strip() or 'Cancelado pelo usuário.'
        try:
            ContaReceberService.cancelar(conta, motivo, request.user)
            messages.success(request, f'Conta #{pk} cancelada.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect(reverse('financeiro:receber_detail', args=[pk]))


class ContaReceberEditarPrazoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'editar'

    def post(self, request, pk):
        filial = _filial(request)
        conta = get_object_or_404(
            ContaReceber.objects.for_filial(filial), pk=pk
        )
        destino = request.POST.get('next') or reverse('financeiro:receber_detail', args=[pk])
        if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}):
            destino = reverse('financeiro:receber_detail', args=[pk])
        nova_data = parse_date(request.POST.get('data_vencimento', '').strip())
        motivo = request.POST.get('motivo', '').strip()
        if not nova_data:
            messages.error(request, 'Informe uma data de vencimento válida.')
            return redirect(destino)
        try:
            ContaReceberService.alterar_prazo(conta, nova_data, motivo, request.user)
            messages.success(request, f'Prazo da conta #{pk} atualizado.')
        except DomainError as exc:
            messages.error(request, str(exc))
        return redirect(destino)
