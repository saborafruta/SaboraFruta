"""Tela CRM → Alertas de Recompra."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.crm.models import RecompraCliente
from apps.crm.services import RecompraService


class AlertasRecompraView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/alertas_recompra.html'

    def get(self, request):
        filial = request.filial_ativa

        # Revalida em lote se os dados estiverem velhos. É barato no caso
        # comum (só lê a linha de controle) e mantém a tela correta mesmo
        # sem um agendador rodando.
        RecompraService.recalcular_se_obsoleto(filial)

        filiais_escopo = (
            Filial.objects.filter(empresa=filial.empresa)
            if filial.is_matriz
            else Filial.objects.filter(pk=filial.pk)
        )

        qs = (
            RecompraCliente.objects
            .filter(filial__in=filiais_escopo)
            .select_related('cliente', 'representante', 'filial')
        )

        # --- Filtros ---
        f = {
            'representante': request.GET.get('representante', '').strip(),
            'cidade': request.GET.get('cidade', '').strip(),
            'uf': request.GET.get('uf', '').strip(),
            'grupo': request.GET.get('grupo', '').strip(),
            'filial': request.GET.get('filial', '').strip(),
            'frequencia': request.GET.get('frequencia', '').strip(),
            'status': request.GET.get('status', '').strip(),
            'q': request.GET.get('q', '').strip(),
        }
        if f['representante']:
            qs = qs.filter(representante_id=f['representante'])
        if f['cidade']:
            qs = qs.filter(cliente__cidade__iexact=f['cidade'])
        if f['uf']:
            qs = qs.filter(cliente__uf=f['uf'])
        if f['grupo']:
            qs = qs.filter(cliente__grupo_desconto=f['grupo'])
        if f['filial']:
            qs = qs.filter(filial_id=f['filial'])
        if f['frequencia']:
            qs = qs.filter(frequencia=f['frequencia'])
        if f['status']:
            qs = qs.filter(status=f['status'])
        if f['q']:
            qs = qs.filter(
                Q(cliente__razao_social__icontains=f['q'])
                | Q(cliente__nome_fantasia__icontains=f['q'])
                | Q(cliente__cpf_cnpj__icontains=f['q'])
            )

        # --- KPIs (sobre o conjunto filtrado) ---
        F = RecompraCliente.Frequencia
        S = RecompraCliente.Status
        agg = qs.aggregate(
            semanais=Count('id', filter=Q(frequencia=F.SEMANAL)),
            quinzenais=Count('id', filter=Q(frequencia=F.QUINZENAL)),
            mensais=Count('id', filter=Q(frequencia=F.MENSAL)),
            personalizados=Count('id', filter=Q(frequencia=F.PERSONALIZADA)),
            sem_padrao=Count('id', filter=Q(frequencia=F.SEM_PADRAO)),
            em_atraso=Count('id', filter=Q(status=S.VERMELHO)),
            proximos=Count('id', filter=Q(status=S.AMARELO)),
            # Quanto de faturamento está "parado" nos clientes atrasados,
            # estimado pelo ticket médio de cada um.
            valor_potencial=Sum('valor_medio', filter=Q(status=S.VERMELHO)),
        )
        kpis = {k: (v or 0) for k, v in agg.items()}
        kpis['valor_potencial'] = agg['valor_potencial'] or Decimal('0')

        # A ordenação por score já entrega a prioridade pedida: quem está
        # mais atrasado em relação ao próprio ritmo sobe, e entre iguais
        # vence quem compra mais alto e com mais regularidade.
        qs = qs.order_by('-score', 'dias_restantes')

        paginator = Paginator(qs, 50)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        querystring = request.GET.copy()
        querystring.pop('page', None)

        # --- Opções dos filtros ---
        from apps.cadastros.models import Cliente, Representante

        clientes_no_escopo = Cliente.objects.filter(
            recompras__filial__in=filiais_escopo,
        ).distinct()

        return render(request, self.template_name, {
            'title': 'Alertas de Recompra',
            'page_obj': page_obj,
            'kpis': kpis,
            'filtros': f,
            'total_resultados': paginator.count,
            'querystring': querystring.urlencode(),
            'representantes': Representante.objects.filter(
                filial__in=filiais_escopo, ativo=True,
            ).distinct().order_by('nome'),
            'cidades': clientes_no_escopo.exclude(cidade='').values_list(
                'cidade', flat=True,
            ).distinct().order_by('cidade'),
            'ufs': clientes_no_escopo.exclude(uf='').values_list(
                'uf', flat=True,
            ).distinct().order_by('uf'),
            'grupos': clientes_no_escopo.exclude(grupo_desconto='').values_list(
                'grupo_desconto', flat=True,
            ).distinct().order_by('grupo_desconto'),
            'filiais': filiais_escopo.order_by('nome_fantasia'),
            'frequencias': RecompraCliente.Frequencia.choices,
            'status_choices': RecompraCliente.Status.choices,
            'hoje': timezone.localdate(),
        })


class RecompraRecalcularView(PermissaoRequiredMixin, View):
    """Força o recálculo agora, sem esperar a janela de obsolescência."""

    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def post(self, request):
        try:
            qtd = RecompraService.recalcular(request.filial_ativa)
            messages.success(request, f'Padrão de recompra recalculado: {qtd} cliente(s).')
        except Exception as exc:
            messages.error(request, f'Falha ao recalcular: {exc}')
        return redirect(reverse('crm:recompra'))
