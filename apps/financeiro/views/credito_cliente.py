"""Views do módulo Crédito de Cliente."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.cadastros.models import Cliente
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.financeiro.models.credito_cliente import CreditoCliente


def _filial(request):
    return request.filial_ativa


def _kpis(qs_base):
    disponivel = qs_base.filter(status=CreditoCliente.Status.DISPONIVEL).aggregate(
        total=Sum('valor'), utilizado=Sum('valor_utilizado')
    )
    total_disp = (disponivel['total'] or Decimal('0')) - (disponivel['utilizado'] or Decimal('0'))
    qtd_disp = qs_base.filter(status=CreditoCliente.Status.DISPONIVEL).count()

    total_util = qs_base.filter(status=CreditoCliente.Status.UTILIZADO).aggregate(
        total=Sum('valor_utilizado')
    )['total'] or Decimal('0')

    return {
        'kpi_saldo_disponivel': total_disp,
        'kpi_qtd_disponivel': qtd_disp,
        'kpi_total_utilizado': total_util,
        'kpi_qtd_total': qs_base.count(),
    }


class CreditoClienteListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request):
        filial = _filial(request)
        qs = CreditoCliente.objects.filter(filial=filial).select_related('cliente', 'usuario')

        q = request.GET.get('q', '').strip()
        status = request.GET.get('status', '')

        if q:
            qs = qs.filter(
                Q(cliente__razao_social__icontains=q)
                | Q(cliente__nome_fantasia__icontains=q)
                | Q(cliente__cpf_cnpj__icontains=q)
                | Q(documento_numero__icontains=q)
            )
        if status:
            qs = qs.filter(status=status)

        kpis = _kpis(CreditoCliente.objects.filter(filial=filial))
        paginator = Paginator(qs.order_by('-created_at'), 25)
        page = paginator.get_page(request.GET.get('page'))

        return render(request, 'financeiro/credito_cliente/list.html', {
            'title': 'Créditos de Clientes',
            'page_obj': page,
            'q': q,
            'status_filtro': status,
            'status_choices': CreditoCliente.Status.choices,
            **kpis,
            'pode_criar': True,
        })


class CreditoClienteDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'ver'

    def get(self, request, pk):
        filial = _filial(request)
        credito = get_object_or_404(CreditoCliente, pk=pk, filial=filial)
        return render(request, 'financeiro/credito_cliente/detail.html', {
            'title': f'Crédito #{credito.pk}',
            'credito': credito,
        })

    @transaction.atomic
    def post(self, request, pk):
        filial = _filial(request)
        credito = get_object_or_404(CreditoCliente, pk=pk, filial=filial)
        acao = request.POST.get('acao')

        if acao == 'cancelar':
            if credito.status != CreditoCliente.Status.DISPONIVEL:
                messages.error(request, 'Apenas créditos disponíveis podem ser cancelados.')
                return redirect(reverse('financeiro:credito_detail', args=[pk]))
            credito.status = CreditoCliente.Status.CANCELADO
            credito.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Crédito #{pk} cancelado com sucesso.')
            return redirect(reverse('financeiro:credito_list'))

        messages.error(request, 'Ação inválida.')
        return redirect(reverse('financeiro:credito_detail', args=[pk]))


class CreditoClienteCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'financeiro'
    permissao_acao = 'criar'

    def get(self, request):
        return render(request, 'financeiro/credito_cliente/form.html', {
            'title': 'Novo Crédito de Cliente',
            'motivo_choices': [
                ('ajuste_comercial', 'Ajuste Comercial'),
                ('cortesia', 'Cortesia'),
                ('devolucao', 'Devolução'),
                ('outro', 'Outro'),
            ],
        })

    @transaction.atomic
    def post(self, request):
        filial = _filial(request)
        cliente_id = request.POST.get('cliente_id')
        valor_raw = request.POST.get('valor', '0').replace(',', '.')
        motivo = request.POST.get('motivo', 'ajuste_comercial')
        documento_numero = request.POST.get('documento_numero', '').strip()
        observacao = request.POST.get('observacao', '').strip()

        erros = []
        if not cliente_id:
            erros.append('Selecione um cliente.')
        try:
            valor = Decimal(valor_raw)
            if valor <= 0:
                erros.append('O valor deve ser maior que zero.')
        except Exception:
            erros.append('Valor inválido.')
        if not observacao:
            erros.append('Informe a observação.')

        if erros:
            for e in erros:
                messages.error(request, e)
            return render(request, 'financeiro/credito_cliente/form.html', {
                'title': 'Novo Crédito de Cliente',
                'motivo_choices': [
                    ('ajuste_comercial', 'Ajuste Comercial'),
                    ('cortesia', 'Cortesia'),
                    ('devolucao', 'Devolução'),
                    ('outro', 'Outro'),
                ],
                'form_data': request.POST,
            })

        try:
            cliente = Cliente.objects.for_filial(filial).get(pk=cliente_id, ativo=True)
        except Cliente.DoesNotExist:
            messages.error(request, 'Cliente não encontrado.')
            return redirect(reverse('financeiro:credito_criar'))

        credito = CreditoCliente.objects.create(
            filial=filial,
            cliente=cliente,
            valor=valor,
            valor_utilizado=Decimal('0'),
            motivo=motivo,
            documento_numero=documento_numero,
            observacao=observacao,
            usuario=request.user,
            status=CreditoCliente.Status.DISPONIVEL,
        )
        messages.success(request, f'Crédito de R$ {valor:.2f} criado para {cliente}.')
        return redirect(reverse('financeiro:credito_detail', args=[credito.pk]))


# ── JSON API para PDV ──────────────────────────────────────────────────────────

def api_credito_saldo(request):
    """Retorna saldo disponível de crédito para um cliente (para uso no PDV)."""
    from apps.core.services.permissions import requer_permissao
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Não autenticado.'}, status=401)

    cliente_id = request.GET.get('cliente_id')
    if not cliente_id:
        return JsonResponse({'saldo': '0.00', 'creditos': []})

    filial = request.filial_ativa
    creditos = CreditoCliente.objects.filter(
        filial=filial,
        cliente_id=cliente_id,
        status=CreditoCliente.Status.DISPONIVEL,
    ).order_by('created_at')

    lista = []
    saldo_total = Decimal('0')
    for c in creditos:
        saldo = c.valor_saldo
        if saldo > 0:
            saldo_total += saldo
            lista.append({
                'id': c.pk,
                'valor': str(c.valor),
                'saldo': str(saldo),
                'motivo': c.motivo,
                'created_at': c.created_at.strftime('%d/%m/%Y'),
            })

    return JsonResponse({
        'saldo': str(saldo_total),
        'creditos': lista,
    })
