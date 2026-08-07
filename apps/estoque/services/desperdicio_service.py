"""Servico de Controle de Validade e Desperdicio.

Reaproveita o que ja existe: AlertaVencimento (vencimento) e o alerta de
estoque minimo (apps.estoque.views.alerta) continuam sendo a fonte de
verdade para esses dois casos. Este servico cobre o que ainda nao existe:
estoque parado, produtos sem giro e a apuracao de perdas/desperdicio
(quantidade, valor e evolucao no tempo) para orientar reducao.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.estoque.models.estoque import Estoque, MovimentacaoEstoque

TIPOS_PERDA = [
    MovimentacaoEstoque.TipoOperacao.PERDA,
    MovimentacaoEstoque.TipoOperacao.DETERIORACAO,
    MovimentacaoEstoque.TipoOperacao.BAIXA_VALIDADE,
    MovimentacaoEstoque.TipoOperacao.ROUBO,
    MovimentacaoEstoque.TipoOperacao.QUEBRA,
]

DIAS_ESTOQUE_PARADO = 30
DIAS_SEM_GIRO = 45


class DesperdicioService:

    @staticmethod
    def produtos_parados(filial, dias: int = DIAS_ESTOQUE_PARADO):
        """Produtos com saldo > 0 e nenhuma movimentacao (entrada ou saida)
        ha `dias` dias -- estoque esquecido, parado."""
        limite = timezone.now() - timedelta(days=dias)
        sem_entrada_recente = Q(ultima_entrada__isnull=True) | Q(ultima_entrada__lt=limite)
        sem_saida_recente = Q(ultima_saida__isnull=True) | Q(ultima_saida__lt=limite)
        qs = Estoque.objects.filter(
            filial=filial,
            quantidade_atual__gt=0,
        ).filter(sem_entrada_recente & sem_saida_recente).select_related('produto')
        return qs

    @staticmethod
    def produtos_sem_giro(filial, dias: int = DIAS_SEM_GIRO):
        """Produtos com saldo > 0 e sem NENHUMA saida ha `dias` dias --
        continuam sendo repostos mas nao saem, risco de virar perda."""
        limite = timezone.now() - timedelta(days=dias)
        qs = Estoque.objects.filter(
            filial=filial,
            quantidade_atual__gt=0,
        ).filter(
            Q(ultima_saida__isnull=True) | Q(ultima_saida__lt=limite)
        ).select_related('produto')
        return qs

    @classmethod
    def resumo_perdas(cls, filial, data_inicio: date, data_fim: date):
        """Totais de perdas/desperdicio no periodo: quantidade, valor e
        detalhamento por tipo de operacao (perda, deterioracao, etc)."""
        base = MovimentacaoEstoque.objects.filter(
            filial=filial,
            tipo_operacao__in=TIPOS_PERDA,
            data_movimentacao__date__gte=data_inicio,
            data_movimentacao__date__lte=data_fim,
        )
        totais = base.aggregate(
            quantidade=Sum('quantidade'),
            valor=Sum('valor_total'),
            eventos=Count('id'),
        )
        por_tipo = list(
            base.values('tipo_operacao')
            .annotate(quantidade=Sum('quantidade'), valor=Sum('valor_total'), eventos=Count('id'))
            .order_by('-valor')
        )
        for item in por_tipo:
            item['tipo_label'] = MovimentacaoEstoque.TipoOperacao(item['tipo_operacao']).label
        return {
            'quantidade': totais['quantidade'] or Decimal('0'),
            'valor': totais['valor'] or Decimal('0'),
            'eventos': totais['eventos'] or 0,
            'por_tipo': por_tipo,
        }

    @classmethod
    def por_categoria(cls, filial, data_inicio: date, data_fim: date):
        """Desperdicio agrupado por categoria do produto -- e o proxy de
        'setor' disponivel hoje no cadastro (nao ha campo de setor de
        estoque separado; categoria e' a dimensao que ja existe e reflete
        a area/linha do produto)."""
        base = MovimentacaoEstoque.objects.filter(
            filial=filial,
            tipo_operacao__in=TIPOS_PERDA,
            data_movimentacao__date__gte=data_inicio,
            data_movimentacao__date__lte=data_fim,
        ).select_related('produto__categoria')
        agregado = (
            base.values('produto__categoria__id', 'produto__categoria__nome')
            .annotate(quantidade=Sum('quantidade'), valor=Sum('valor_total'), eventos=Count('id'))
            .order_by('-valor')
        )
        resultado = []
        for item in agregado:
            resultado.append({
                'categoria': item['produto__categoria__nome'] or 'Sem categoria',
                'quantidade': item['quantidade'] or Decimal('0'),
                'valor': item['valor'] or Decimal('0'),
                'eventos': item['eventos'],
            })
        return resultado

    @classmethod
    def evolucao_mensal(cls, filial, meses: int = 6):
        """Serie mensal de perdas (valor) para medir reducao de desperdicio
        ao longo do tempo."""
        hoje = timezone.localdate()
        inicio = (hoje.replace(day=1) - timedelta(days=30 * (meses - 1))).replace(day=1)
        base = MovimentacaoEstoque.objects.filter(
            filial=filial,
            tipo_operacao__in=TIPOS_PERDA,
            data_movimentacao__date__gte=inicio,
        )
        agregado = (
            base.annotate(mes=TruncMonth('data_movimentacao'))
            .values('mes')
            .annotate(valor=Sum('valor_total'), quantidade=Sum('quantidade'))
            .order_by('mes')
        )
        por_mes = {item['mes'].date().replace(day=1): item for item in agregado}

        serie = []
        cursor = inicio
        for _ in range(meses):
            item = por_mes.get(cursor)
            serie.append({
                'mes': cursor,
                'valor': (item['valor'] if item else None) or Decimal('0'),
                'quantidade': (item['quantidade'] if item else None) or Decimal('0'),
            })
            if cursor.month == 12:
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)
        return serie

    @classmethod
    def indicador_reducao(cls, filial):
        """Compara o valor de perdas do mes atual com o mes anterior --
        percentual negativo = desperdicio caindo."""
        serie = cls.evolucao_mensal(filial, meses=2)
        anterior, atual = serie[0]['valor'], serie[1]['valor']
        if not anterior:
            return None
        variacao = ((atual - anterior) / anterior) * Decimal('100')
        return {
            'mes_anterior': serie[0]['mes'],
            'valor_anterior': anterior,
            'mes_atual': serie[1]['mes'],
            'valor_atual': atual,
            'variacao_percentual': variacao,
        }
