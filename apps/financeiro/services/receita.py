"""
Receita realizada de vendas de PDV.

O problema que isto resolve: `VendaPDV.valor_total` é o valor da venda, não a
receita. Formas de pagamento marcadas com `movimenta_caixa=False`
(Doação, Permuta) dão baixa no estoque normalmente, mas **não são receita** —
não entra dinheiro. Somar `valor_total` direto infla o faturamento.

O PDV já respeitava isso no fechamento de caixa
(`venda_pdv_service`, `pdv.views.fechamento`), mas os painéis e relatórios não.
Este módulo centraliza a regra para que as duas visões não divirjam.

Fórmula, idêntica à do caixa:

    receita = valor_total − Σ(pagamento.valor − pagamento.troco)
                            para formas com movimenta_caixa=False

Numa soma por grupo vale a distributiva — Σ(a−b) = Σa − Σb —, então em vez de
percorrer venda por venda (O(n) e N+1 nos pagamentos), faz-se uma agregação do
desconto pelas mesmas chaves do agrupamento e subtrai. É o que
`ajuste_por_grupo` devolve.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import F, Sum


def ajuste_por_grupo(vendas_qs, *campos_grupo) -> dict:
    """
    Quanto descontar do faturamento, agrupado por `campos_grupo`.

    `vendas_qs` é um queryset de VendaPDV já filtrado (período, filial,
    status). Os `campos_grupo` são caminhos a partir de **PagamentoVendaPDV**,
    tipicamente com o prefixo `venda_pdv__`.

    Devolve `{(valor_do_campo1, ...): Decimal}`. Chave de um só campo também
    vem como tupla, para o chamador não precisar de dois caminhos.

    Exemplo:
        ajuste_por_grupo(vendas, 'venda_pdv__data_venda__year',
                                 'venda_pdv__data_venda__month')
    """
    from apps.pdv.models import PagamentoVendaPDV

    if not campos_grupo:
        raise ValueError('informe ao menos um campo de agrupamento')

    linhas = (
        PagamentoVendaPDV.objects
        .filter(venda_pdv__in=vendas_qs, forma_pagamento__movimenta_caixa=False)
        .values(*campos_grupo)
        .annotate(desconto=Sum(F('valor') - F('troco')))
    )
    return {
        tuple(linha[campo] for campo in campos_grupo): linha['desconto'] or Decimal('0')
        for linha in linhas
    }


def ajuste_total(vendas_qs) -> Decimal:
    """Total a descontar do faturamento de `vendas_qs`, sem agrupar."""
    from apps.pdv.models import PagamentoVendaPDV

    total = (
        PagamentoVendaPDV.objects
        .filter(venda_pdv__in=vendas_qs, forma_pagamento__movimenta_caixa=False)
        .aggregate(d=Sum(F('valor') - F('troco')))['d']
    )
    return total or Decimal('0')


def receita_total(vendas_qs) -> Decimal:
    """Faturamento de `vendas_qs` já descontadas as formas não contabilizadas."""
    bruto = vendas_qs.aggregate(t=Sum('valor_total'))['t'] or Decimal('0')
    liquido = Decimal(str(bruto)) - ajuste_total(vendas_qs)
    return max(Decimal('0'), liquido)


def ajuste_por_cliente(vendas_qs) -> dict:
    """Desconto por cliente — para a Curva ABC e indicadores por cliente."""
    return {
        chave[0]: valor
        for chave, valor in ajuste_por_grupo(vendas_qs, 'venda_pdv__cliente_id').items()
    }


def formas_nao_contabilizadas(empresa_id) -> set[str]:
    """
    Descrições das formas que não são receita, para os painéis marcarem as
    linhas em vez de simplesmente escondê-las: saber que houve 10 permutas
    tem valor gerencial, o que não pode é somá-las ao faturamento.
    """
    from apps.financeiro.models import FormaPagamento

    return set(
        FormaPagamento.objects
        .filter(empresa_id=empresa_id, movimenta_caixa=False)
        .values_list('descricao', flat=True)
    )
