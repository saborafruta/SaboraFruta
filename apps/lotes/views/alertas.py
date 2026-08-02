"""Painel de alertas de vencimento — 6 faixas operacionais."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

import csv

from apps.core.services.permissions import PermissaoRequiredMixin
from apps.estoque.models import AlertaVencimento, LoteProduto

# Configuração das 6 faixas: (chave, dias_ate, label, cor_tailwind, cor_hex_bg, cor_hex_text)
FAIXAS = [
    ('d1',   1,   'Urgente',  'red',    'rgba(239,68,68,0.15)',    '#f87171'),
    ('d7',   7,   'Crítico',  'orange', 'rgba(249,115,22,0.15)',   '#fb923c'),
    ('d30',  30,  'Alto',     'amber',  'rgba(245,158,11,0.15)',   '#fbbf24'),
    ('d60',  60,  'Médio',    'yellow', 'rgba(234,179,8,0.12)',    '#facc15'),
    ('d90',  90,  'Atenção',  'blue',   'rgba(59,130,246,0.12)',   '#60a5fa'),
    ('d180', 180, 'Aviso',    'teal',   'rgba(20,184,166,0.12)',   '#2dd4bf'),
]


def _lotes_por_faixa(filial, hoje):
    """Retorna dict {chave: queryset} com lotes em cada faixa."""
    resultado = {}
    anterior = 0
    for chave, dias, *_ in FAIXAS:
        qs = (
            LoteProduto.objects
            .for_filial(filial)
            .filter(
                status=LoteProduto.Status.ATIVO,
                quantidade_atual__gt=0,
                data_validade__isnull=False,
                data_validade__gte=hoje + timedelta(days=anterior),
                data_validade__lte=hoje + timedelta(days=dias),
            )
            .select_related('produto', 'fornecedor')
            .order_by('data_validade', 'numero_lote')
        )
        resultado[chave] = qs
        anterior = dias
    return resultado


class AlertasVencimentoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'estoque'
    template_name = 'lotes/alertas_vencimento.html'

    def get(self, request):
        filial = request.filial_ativa
        hoje = timezone.localdate()
        faixa_ativa = request.GET.get('faixa', '')

        lotes_por_faixa = _lotes_por_faixa(filial, hoje)

        # Contagens para os cards KPI
        contagens = {chave: qs.count() for chave, qs in lotes_por_faixa.items()}
        total_alertas = sum(contagens.values())

        # Lotes exibidos na tabela (faixa selecionada ou todas)
        if faixa_ativa and faixa_ativa in lotes_por_faixa:
            lotes_tabela = list(lotes_por_faixa[faixa_ativa])
            faixa_info = next((f for f in FAIXAS if f[0] == faixa_ativa), None)
        else:
            # Sem filtro: todos os lotes dentro de 180 dias, ordenados por validade
            lotes_tabela = list(
                LoteProduto.objects
                .for_filial(filial)
                .filter(
                    status=LoteProduto.Status.ATIVO,
                    quantidade_atual__gt=0,
                    data_validade__isnull=False,
                    data_validade__gte=hoje,
                    data_validade__lte=hoje + timedelta(days=180),
                )
                .select_related('produto', 'fornecedor')
                .order_by('data_validade', 'numero_lote')
            )
            faixa_info = None

        if request.GET.get('export') == 'csv':
            return self._exportar_csv(lotes_tabela, hoje)

        # Anota a faixa de cada lote para colorir na tabela
        for lote in lotes_tabela:
            lote.faixa = _classificar_faixa(lote.dias_para_vencer)

        return render(request, self.template_name, {
            'faixas': FAIXAS,
            'faixa_ativa': faixa_ativa,
            'faixa_info': faixa_info,
            'contagens': contagens,
            'total_alertas': total_alertas,
            'lotes_tabela': lotes_tabela,
            'hoje': hoje,
        })

    def post(self, request):
        """Resolve alerta manualmente (marcar como tratado)."""
        lote_pk = request.POST.get('lote_pk')
        if lote_pk:
            AlertaVencimento.objects.filter(
                lote_id=lote_pk,
                filial=request.filial_ativa,
                resolvido=False,
            ).update(resolvido=True, notificado_em=timezone.now())
        return redirect(request.META.get('HTTP_REFERER', 'lotes:alertas'))

    @staticmethod
    def _exportar_csv(lotes, hoje):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="alertas_vencimento.csv"'
        response.write('﻿')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Faixa', 'Número do Lote', 'Produto', 'Fornecedor',
            'Validade', 'Dias para Vencer', 'Quantidade', 'Custo Unit.',
        ])
        for lote in lotes:
            faixa = _classificar_faixa(lote.dias_para_vencer)
            writer.writerow([
                faixa,
                lote.numero_lote,
                lote.produto.descricao,
                lote.fornecedor.razao_social if lote.fornecedor_id else '',
                lote.data_validade.strftime('%d/%m/%Y') if lote.data_validade else '',
                lote.dias_para_vencer,
                lote.quantidade_atual,
                lote.custo_unitario,
            ])
        return response


def _classificar_faixa(dias):
    """Retorna a chave da faixa dado o número de dias para vencer."""
    if dias is None:
        return ''
    for chave, limite, *_ in FAIXAS:
        if dias <= limite:
            return chave
    return ''
