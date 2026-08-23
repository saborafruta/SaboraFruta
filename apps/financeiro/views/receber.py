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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.constants.enums import StatusContaReceber
from apps.financeiro.forms.receber import BaixaContaReceberForm, ContaReceberForm
from apps.financeiro.models.receber_pagar import ContaReceber
from apps.financeiro.services.receber_service import ContaReceberService
from apps.financeiro.services.dashboard_contas_service import DashboardContasService

STATUS_CHOICES = StatusContaReceber.choices

PILL_STATUS = {
    StatusContaReceber.ABERTO:     'is-blue',
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
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO, StatusContaReceber.NEGOCIADO]
    ).aggregate(
        total_aberto=Sum('valor_saldo'),
        qtd_aberto=Count('id'),
    )

    vencido = qs_base.filter(
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
        data_vencimento__lt=hoje,
    ).aggregate(total_vencido=Sum('valor_saldo'))

    recebido_mes = qs_base.filter(
        status=StatusContaReceber.PAGO,
        data_pagamento__gte=primeiro_dia_mes,
    ).aggregate(total_mes=Sum('valor_pago'))

    vence_hoje = qs_base.filter(
        status__in=[StatusContaReceber.ABERTO, StatusContaReceber.VENCIDO],
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
            .select_related('cliente', 'forma_pagamento')
            .order_by('data_vencimento', 'cliente__razao_social')
        )

        kpis = _kpis(qs)

        # Filtros
        status = request.GET.get('status', '')
        q = request.GET.get('q', '').strip()
        data_ini = request.GET.get('data_ini', '')
        data_fim = request.GET.get('data_fim', '')

        if status:
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


class ContaReceberCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def get(self, request):
        filial = _filial(request)
        form = ContaReceberForm(filial=filial)
        return render(request, 'financeiro/receber/form.html', {
            'title': 'Nova Conta a Receber',
            'form': form,
            'cancel_url': reverse('financeiro:receber_list'),
        })

    def post(self, request):
        filial = _filial(request)
        form = ContaReceberForm(request.POST, filial=filial)
        if not form.is_valid():
            return render(request, 'financeiro/receber/form.html', {
                'title': 'Nova Conta a Receber',
                'form': form,
                'cancel_url': reverse('financeiro:receber_list'),
            })

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
            messages.success(request, f'Conta a receber #{conta.pk} lançada com sucesso.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/receber/form.html', {
                'title': 'Nova Conta a Receber',
                'form': form,
                'cancel_url': reverse('financeiro:receber_list'),
            })

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

    def get(self, request, pk):
        conta = self._get_conta(request, pk)
        filial = _filial(request)

        if conta.status in [StatusContaReceber.PAGO, StatusContaReceber.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser baixada.')
            return redirect(reverse('financeiro:receber_detail', args=[pk]))

        form = BaixaContaReceberForm(filial=filial, conta=conta)
        return render(request, 'financeiro/receber/baixa.html', {
            'title': f'Receber — #{conta.pk}',
            'conta': conta,
            'form': form,
            'cancel_url': reverse('financeiro:receber_detail', args=[pk]),
        })

    def post(self, request, pk):
        conta = self._get_conta(request, pk)
        filial = _filial(request)

        if conta.status in [StatusContaReceber.PAGO, StatusContaReceber.CANCELADO]:
            messages.warning(request, 'Esta conta não pode ser baixada.')
            return redirect(reverse('financeiro:receber_detail', args=[pk]))

        form = BaixaContaReceberForm(request.POST, filial=filial, conta=conta)
        if not form.is_valid():
            return render(request, 'financeiro/receber/baixa.html', {
                'title': f'Receber — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:receber_detail', args=[pk]),
            })

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
            )
            if conta.status == StatusContaReceber.PAGO:
                messages.success(request, f'Conta #{pk} recebida integralmente. ✓')
            else:
                messages.success(request, f'Baixa parcial registrada. Saldo restante: R$ {conta.valor_saldo:,.2f}.')
        except DomainError as exc:
            messages.error(request, str(exc))
            return render(request, 'financeiro/receber/baixa.html', {
                'title': f'Receber — #{conta.pk}',
                'conta': conta,
                'form': form,
                'cancel_url': reverse('financeiro:receber_detail', args=[pk]),
            })

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
