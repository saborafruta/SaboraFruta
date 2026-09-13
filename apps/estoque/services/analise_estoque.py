"""
Duas leituras que faltavam no equilibrio de estoque: curva ABC por giro
(volume vendido, não receita) e produtos acima do estoque máximo.

A curva ABC que já existe no dashboard (`DashboardView._classificar_abc`)
é por receita -- útil pra saber quem paga as contas, mas não diz quem
GIRA. Um produto de ticket alto e venda rara pode ser classe A por
receita e ainda assim empatar capital parado se ninguém olhar volume.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.estoque.models import Estoque
from apps.pdv.models import ItemVendaPDV
from apps.produtos.models import Produto, ProdutoFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

ZERO = Decimal("0")

STATUS_VENDA_REALIZADA = (
    PedidoVenda.Status.FATURADO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
    PedidoVenda.Status.ENTREGUE,
)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def classificar_abc_giro(*, empresa, filial=None, dias_analise: int = 90) -> dict:
    """
    Classifica os produtos vendidos no período por volume (giro), não por
    receita -- os mesmos limites da curva ABC do dashboard (A até 80% do
    acumulado, B até 95%, C até 100%), pra quem já conhece a leitura não
    precisar aprender outra régua.
    """
    produtos_qs = Produto.objects.for_empresa(empresa).filter(ativo=True)
    if filial is not None:
        produtos_qs = produtos_qs.filter(
            pk__in=ProdutoFilial.objects.filter(filial=filial, ativo=True).values("produto_id"),
        )
    produtos = {p.pk: p for p in produtos_qs.select_related("unidade_medida")}
    if not produtos:
        return {"itens": [], "resumo": {}}

    inicio = timezone.now() - timezone.timedelta(days=dias_analise)
    filial_ids = [filial.pk] if filial is not None else None

    vendido = defaultdict(lambda: ZERO)
    pdv_qs = ItemVendaPDV.objects.filter(
        produto_id__in=produtos, venda_pdv__status="finalizada",
        venda_pdv__data_venda__gte=inicio,
    )
    if filial_ids:
        pdv_qs = pdv_qs.filter(venda_pdv__filial_id__in=filial_ids)
    for row in pdv_qs.values("produto_id").annotate(total=Sum("quantidade")):
        vendido[row["produto_id"]] += _decimal(row["total"])

    b2b_qs = ItemPedidoVenda.objects.filter(
        produto_id__in=produtos, pedido__status__in=STATUS_VENDA_REALIZADA,
        pedido__data_emissao__gte=inicio,
    )
    if filial_ids:
        b2b_qs = b2b_qs.filter(pedido__filial_id__in=filial_ids)
    for row in b2b_qs.values("produto_id").annotate(total=Sum("quantidade")):
        vendido[row["produto_id"]] += _decimal(row["total"])

    linhas = [
        {"produto": produtos[produto_id], "quantidade_vendida": quantidade}
        for produto_id, quantidade in vendido.items()
        if quantidade > ZERO
    ]
    linhas.sort(key=lambda linha: linha["quantidade_vendida"], reverse=True)

    total = sum((linha["quantidade_vendida"] for linha in linhas), ZERO)
    resumo = {
        "A": {"qtd": 0, "quantidade": ZERO}, "B": {"qtd": 0, "quantidade": ZERO},
        "C": {"qtd": 0, "quantidade": ZERO},
    }
    if total > ZERO:
        acumulado = ZERO
        for linha in linhas:
            acumulado += linha["quantidade_vendida"]
            pct_acumulado = acumulado / total * 100
            if pct_acumulado <= 80:
                classe = "A"
            elif pct_acumulado <= 95:
                classe = "B"
            else:
                classe = "C"
            linha["classe"] = classe
            linha["pct_participacao"] = (
                linha["quantidade_vendida"] / total * 100
            ).quantize(Decimal("0.1"))
            linha["pct_acumulado"] = pct_acumulado.quantize(Decimal("0.1"))
            resumo[classe]["qtd"] += 1
            resumo[classe]["quantidade"] += linha["quantidade_vendida"]

    return {"itens": linhas, "resumo": resumo, "total_vendido": total}


def produtos_em_excesso(*, empresa, filial=None) -> list[dict]:
    """
    Produtos com saldo disponível acima do estoque máximo cadastrado --
    capital parado que a curva ABC e o equilíbrio não mostram sozinhos
    (um produto pode estar em excesso na rede inteira, não só desbalanceado
    entre filiais).

    Só entra na lista quem TEM máximo cadastrado (>0): sem essa régua não
    há "acima de quanto" pra comparar, e mostrar todo mundo com máximo
    zero (não configurado) encheria a tela de falso positivo.
    """
    produtos_qs = (
        Produto.objects.for_empresa(empresa)
        .filter(ativo=True, estoque_maximo__gt=0)
        .select_related("unidade_medida")
    )
    if filial is not None:
        produtos_qs = produtos_qs.filter(
            pk__in=ProdutoFilial.objects.filter(filial=filial, ativo=True).values("produto_id"),
        )
    produtos = {p.pk: p for p in produtos_qs}
    if not produtos:
        return []

    estoque_qs = Estoque.objects.filter(produto_id__in=produtos)
    if filial is not None:
        estoque_qs = estoque_qs.filter(filial=filial)
    saldos = defaultdict(lambda: ZERO)
    for row in estoque_qs.values("produto_id").annotate(total=Sum("quantidade_disponivel")):
        saldos[row["produto_id"]] += _decimal(row["total"])

    itens = []
    for produto_id, saldo in saldos.items():
        produto = produtos.get(produto_id)
        if produto is None:
            continue
        maximo = _decimal(produto.estoque_maximo)
        if saldo <= maximo:
            continue
        itens.append({
            "produto": produto,
            "saldo": saldo,
            "estoque_maximo": maximo,
            "excedente": (saldo - maximo).quantize(Decimal("0.001")),
        })
    itens.sort(key=lambda item: item["excedente"], reverse=True)
    return itens
