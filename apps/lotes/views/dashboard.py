"""Dashboard principal do módulo de Lotes."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import LoteProduto
from apps.lotes.models import InspecaoLote
from apps.lotes.services.fefo_service import sugerir_lotes_fefo


class LotesDashboardView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'lotes/dashboard.html'

    def get(self, request):
        filial = request.filial_ativa
        hoje = timezone.localdate()

        qs_base = LoteProduto.objects.for_filial(filial)

        total_ativos = qs_base.filter(status=LoteProduto.Status.ATIVO, quantidade_atual__gt=0).count()
        total_vencendo_7 = qs_base.filter(
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
            data_validade__isnull=False,
            data_validade__gte=hoje,
            data_validade__lte=hoje + timedelta(days=7),
        ).count()
        total_vencendo_30 = qs_base.filter(
            status=LoteProduto.Status.ATIVO,
            quantidade_atual__gt=0,
            data_validade__isnull=False,
            data_validade__gte=hoje,
            data_validade__lte=hoje + timedelta(days=30),
        ).count()
        total_vencidos = qs_base.filter(
            Q(status=LoteProduto.Status.VENCIDO) | Q(data_validade__lt=hoje, status=LoteProduto.Status.ATIVO)
        ).filter(quantidade_atual__gt=0).count()

        valor_total = (
            qs_base
            .filter(status=LoteProduto.Status.ATIVO, quantidade_atual__gt=0)
            .aggregate(total=Sum('quantidade_atual'))['total'] or Decimal('0')
        )

        # Alerta FEFO: lotes que devem sair primeiro (mais perto do vencimento)
        alerta_fefo = (
            qs_base
            .filter(
                status=LoteProduto.Status.ATIVO,
                quantidade_atual__gt=0,
                data_validade__isnull=False,
                data_validade__gte=hoje,
            )
            .select_related('produto', 'fornecedor')
            .order_by('data_validade')[:15]
        )

        # Últimas inspeções
        ultimas_inspecoes = (
            InspecaoLote.objects
            .filter(lote__filial=filial)
            .select_related('lote', 'lote__produto', 'responsavel')
            .order_by('-data_inspecao')[:10]
        )

        # Lotes em quarentena/bloqueados
        lotes_bloqueados = (
            qs_base
            .filter(status__in=[LoteProduto.Status.BLOQUEADO, LoteProduto.Status.QUARENTENA])
            .filter(quantidade_atual__gt=0)
            .select_related('produto')
            .order_by('status', 'created_at')[:10]
        )

        return render(request, self.template_name, {
            'total_ativos': total_ativos,
            'total_vencendo_7': total_vencendo_7,
            'total_vencendo_30': total_vencendo_30,
            'total_vencidos': total_vencidos,
            'alerta_fefo': alerta_fefo,
            'ultimas_inspecoes': ultimas_inspecoes,
            'lotes_bloqueados': lotes_bloqueados,
        })
