"""Indicadores consolidados de contas a pagar e a receber."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from apps.financeiro.constants.enums import StatusContaPagar, StatusContaReceber
from apps.financeiro.models.conta_bancaria import ContaBancaria
from apps.financeiro.models.receber_pagar import ContaPagar, ContaReceber, PagamentoContaPagar


ZERO = Decimal('0')


def _valor(dados, chave):
    return dados.get(chave) or ZERO


def _percentual(valor, referencia):
    if not valor or not referencia:
        return 0
    return max(3, min(100, round((valor / referencia) * 100)))


def _adicionar_percentuais(itens):
    maior = max([abs(item['total']) for item in itens] or [ZERO])
    for item in itens:
        item['percentual'] = _percentual(abs(item['total']), maior)
    return itens


class DashboardContasService:
    @classmethod
    def apurar(cls, filial, hoje=None):
        from django.utils import timezone

        hoje = hoje or timezone.localdate()
        inicio_mes = hoje.replace(day=1)
        sete_dias = hoje + timedelta(days=7)
        trinta_dias = hoje + timedelta(days=30)

        status_receber = [
            StatusContaReceber.ABERTO,
            StatusContaReceber.VENCIDO,
            StatusContaReceber.NEGOCIADO,
        ]
        status_pagar = [
            StatusContaPagar.ABERTO,
            StatusContaPagar.VENCIDO,
            StatusContaPagar.AGENDADO,
        ]

        receber_qs = ContaReceber.objects.for_filial(filial)
        pagar_qs = ContaPagar.objects.for_filial(filial)

        receber = receber_qs.aggregate(
            aberto=Sum('valor_saldo', filter=Q(status__in=status_receber)),
            qtd_aberto=Count('id', filter=Q(status__in=status_receber)),
            vencido=Sum('valor_saldo', filter=Q(status__in=status_receber, data_vencimento__lt=hoje)),
            qtd_vencido=Count('id', filter=Q(status__in=status_receber, data_vencimento__lt=hoje)),
            hoje=Sum('valor_saldo', filter=Q(status__in=status_receber, data_vencimento=hoje)),
            sete=Sum('valor_saldo', filter=Q(status__in=status_receber, data_vencimento__gt=hoje, data_vencimento__lte=sete_dias)),
            trinta=Sum('valor_saldo', filter=Q(status__in=status_receber, data_vencimento__gt=sete_dias, data_vencimento__lte=trinta_dias)),
            futuro=Sum('valor_saldo', filter=Q(status__in=status_receber, data_vencimento__gt=trinta_dias)),
            realizado_mes=Sum(
                'valor_pago',
                filter=Q(
                    valor_pago__gt=0,
                    data_pagamento__gte=inicio_mes,
                    data_pagamento__lte=hoje,
                ),
            ),
        )
        pagar = pagar_qs.aggregate(
            aberto=Sum('valor_saldo', filter=Q(status__in=status_pagar)),
            qtd_aberto=Count('id', filter=Q(status__in=status_pagar)),
            vencido=Sum('valor_saldo', filter=Q(status__in=status_pagar, data_vencimento__lt=hoje)),
            qtd_vencido=Count('id', filter=Q(status__in=status_pagar, data_vencimento__lt=hoje)),
            hoje=Sum('valor_saldo', filter=Q(status__in=status_pagar, data_vencimento=hoje)),
            sete=Sum('valor_saldo', filter=Q(status__in=status_pagar, data_vencimento__gt=hoje, data_vencimento__lte=sete_dias)),
            trinta=Sum('valor_saldo', filter=Q(status__in=status_pagar, data_vencimento__gt=sete_dias, data_vencimento__lte=trinta_dias)),
            futuro=Sum('valor_saldo', filter=Q(status__in=status_pagar, data_vencimento__gt=trinta_dias)),
            sem_categoria=Sum(
                'valor_saldo',
                filter=Q(
                    status__in=status_pagar,
                    plano_contas__isnull=True,
                    data_vencimento__lte=trinta_dias,
                ),
            ),
        )

        pagamentos_mes_qs = PagamentoContaPagar.objects.for_filial(filial).filter(
            data_pagamento__gte=inicio_mes,
            data_pagamento__lte=hoje,
        )
        pagar['realizado_mes'] = (
            pagamentos_mes_qs.aggregate(total=Sum('valor_pago'))['total'] or ZERO
        )

        for dados in (receber, pagar):
            for chave in ('aberto', 'vencido', 'hoje', 'sete', 'trinta', 'futuro', 'realizado_mes'):
                dados[chave] = _valor(dados, chave)
        pagar['sem_categoria'] = _valor(pagar, 'sem_categoria')

        agenda = [
            {'chave': 'vencido', 'label': 'Vencido', 'receber': receber['vencido'], 'pagar': pagar['vencido']},
            {'chave': 'hoje', 'label': 'Hoje', 'receber': receber['hoje'], 'pagar': pagar['hoje']},
            {'chave': 'sete', 'label': 'Próximos 7 dias', 'receber': receber['sete'], 'pagar': pagar['sete']},
            {'chave': 'trinta', 'label': 'De 8 a 30 dias', 'receber': receber['trinta'], 'pagar': pagar['trinta']},
            {'chave': 'futuro', 'label': 'Após 30 dias', 'receber': receber['futuro'], 'pagar': pagar['futuro']},
        ]
        maior_agenda = max(
            [item[lado] for item in agenda for lado in ('receber', 'pagar')] or [ZERO]
        )
        for item in agenda:
            item['receber_pct'] = _percentual(item['receber'], maior_agenda)
            item['pagar_pct'] = _percentual(item['pagar'], maior_agenda)

        maiores_clientes = list(
            receber_qs.filter(status__in=status_receber, data_vencimento__lte=trinta_dias)
            .values('cliente__razao_social')
            .annotate(total=Sum('valor_saldo'))
            .order_by('-total')[:5]
        )
        for item in maiores_clientes:
            item['nome'] = item['cliente__razao_social'] or 'Cliente não identificado'
        _adicionar_percentuais(maiores_clientes)

        formas_realizadas = []
        recebimentos_por_forma = (
            receber_qs.filter(
                valor_pago__gt=0,
                data_pagamento__gte=inicio_mes,
                data_pagamento__lte=hoje,
            )
            .values('forma_pagamento__descricao')
            .annotate(total=Sum('valor_pago'))
        )
        for item in recebimentos_por_forma:
            formas_realizadas.append({
                'nome': item['forma_pagamento__descricao'] or 'Não informada',
                'natureza': 'Recebido',
                'tipo': 'receber',
                'total': item['total'] or ZERO,
            })

        pagamentos_por_forma = (
            pagamentos_mes_qs
            .values('forma_pagamento__descricao')
            .annotate(total=Sum('valor_pago'))
        )
        for item in pagamentos_por_forma:
            formas_realizadas.append({
                'nome': item['forma_pagamento__descricao'] or 'Não informada',
                'natureza': 'Pago',
                'tipo': 'pagar',
                'total': item['total'] or ZERO,
            })
        formas_realizadas = sorted(
            formas_realizadas,
            key=lambda registro: registro['total'],
            reverse=True,
        )[:5]
        _adicionar_percentuais(formas_realizadas)

        contas_bancarias_qs = ContaBancaria.objects.for_filial(filial).filter(ativo=True)
        saldo_bancario_total = (
            contas_bancarias_qs.aggregate(total=Sum('saldo_atual'))['total'] or ZERO
        )
        contas_bancarias = [
            {
                'nome': conta.descricao or f'{conta.banco_nome} · {conta.agencia}/{conta.conta}',
                'total': conta.saldo_atual,
            }
            for conta in contas_bancarias_qs.order_by('-saldo_atual')[:5]
        ]
        _adicionar_percentuais(contas_bancarias)

        maiores_categorias = list(
            pagar_qs.filter(
                status__in=status_pagar,
                plano_contas__isnull=False,
                data_vencimento__lte=trinta_dias,
            )
            .values('plano_contas__descricao')
            .annotate(total=Sum('valor_saldo'))
            .order_by('-total')[:5]
        )
        for item in maiores_categorias:
            item['nome'] = item['plano_contas__descricao'] or 'Sem categoria'
        _adicionar_percentuais(maiores_categorias)

        saldo_projetado = receber['aberto'] - pagar['aberto']
        saldo_projetado_com_bancos = saldo_bancario_total + saldo_projetado
        saldo_realizado_mes = receber['realizado_mes'] - pagar['realizado_mes']

        return {
            'hoje': hoje,
            'inicio_mes': inicio_mes,
            'sete_dias': sete_dias,
            'trinta_dias': trinta_dias,
            'receber': receber,
            'pagar': pagar,
            'saldo_projetado': saldo_projetado,
            'saldo_projetado_abs': abs(saldo_projetado),
            'saldo_bancario_total': saldo_bancario_total,
            'saldo_projetado_com_bancos': saldo_projetado_com_bancos,
            'saldo_realizado_mes': saldo_realizado_mes,
            'saldo_realizado_mes_abs': abs(saldo_realizado_mes),
            'agenda': agenda,
            'maiores_clientes': maiores_clientes,
            'formas_realizadas': formas_realizadas,
            'contas_bancarias': contas_bancarias,
            'maiores_categorias': maiores_categorias,
        }
