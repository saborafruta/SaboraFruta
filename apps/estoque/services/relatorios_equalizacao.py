"""Relatórios de equalização que ainda não tinham tela própria (Fase 27)."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Filial
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.posicoes_estoque import calcular_posicoes, custo_unitario
from apps.produtos.models import Produto

ZERO = Decimal("0")


def relatorio_ruptura(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> list[dict]:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    itens = [p for p in posicoes if p["classe"] == "ruptura"]
    itens.sort(key=lambda p: p["demanda_diaria"], reverse=True)
    return itens


def relatorio_produtos_parados(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14) -> list[dict]:
    posicoes = calcular_posicoes(empresa=empresa, dias_analise=dias_analise, dias_cobertura=dias_cobertura)
    itens = [p for p in posicoes if p["parado"] and p["saldo"] > ZERO]
    itens.sort(key=lambda p: p["valor"], reverse=True)
    return itens


def relatorio_transferencias_por_filial(*, empresa, dias: int = 30) -> list[dict]:
    inicio = timezone.now() - timezone.timedelta(days=dias)
    filiais = {f.pk: f for f in Filial.objects.filter(empresa=empresa, ativo=True)}
    filial_ids = list(filiais.keys())

    movs = (
        MovimentacaoEstoque.objects.filter(
            filial_id__in=filial_ids, tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            data_movimentacao__gte=inicio, transferencia_cancelada=False,
        )
        .values("filial_id", "produto_id")
        .annotate(quantidade=Sum("quantidade"))
    )
    produtos = {p.pk: p for p in Produto.objects.filter(pk__in={row["produto_id"] for row in movs})}

    totais = {filial_id: {"quantidade": ZERO, "valor": ZERO} for filial_id in filial_ids}
    for row in movs:
        produto = produtos.get(row["produto_id"])
        if not produto:
            continue
        quantidade = Decimal(str(row["quantidade"]))
        totais[row["filial_id"]]["quantidade"] += quantidade
        totais[row["filial_id"]]["valor"] += quantidade * custo_unitario(produto)

    itens = [
        {"filial": filiais[filial_id], "quantidade": dados["quantidade"], "valor": dados["valor"].quantize(Decimal("0.01"))}
        for filial_id, dados in totais.items() if dados["quantidade"] > ZERO
    ]
    itens.sort(key=lambda item: item["valor"], reverse=True)
    return itens


def relatorio_produtos_mais_transferidos(*, empresa, dias: int = 30, limite: int = 20) -> list[dict]:
    inicio = timezone.now() - timezone.timedelta(days=dias)
    filial_ids = list(Filial.objects.filter(empresa=empresa, ativo=True).values_list("pk", flat=True))

    movs = (
        MovimentacaoEstoque.objects.filter(
            filial_id__in=filial_ids, tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            data_movimentacao__gte=inicio, transferencia_cancelada=False,
        )
        .values("produto_id")
        .annotate(quantidade=Sum("quantidade"))
        .order_by("-quantidade")[:limite]
    )
    produtos = {p.pk: p for p in Produto.objects.filter(pk__in=[row["produto_id"] for row in movs])}

    itens = []
    for row in movs:
        produto = produtos.get(row["produto_id"])
        if not produto:
            continue
        quantidade = Decimal(str(row["quantidade"]))
        itens.append({
            "produto": produto, "quantidade": quantidade,
            "valor": (quantidade * custo_unitario(produto)).quantize(Decimal("0.01")),
        })
    return itens
