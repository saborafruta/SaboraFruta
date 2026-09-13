"""
Posição de estoque por produto+filial, para as telas de visão consolidada
(dashboard, mapa e central de equalização).

Não é o motor de sugestão de transferência (`equilibrio_estoque.calcular_equilibrio`,
que tem sua própria lógica testada de meta com redistribuição de excedente
de rede) -- aqui a meta é simplificada (reserva pura, sem o bônus de
redistribuição) porque essas telas só precisam classificar a situação de
cada posição, não decidir quanto mover de onde. Para "quanto sugerir
transferir", use `calcular_equilibrio`.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.models import Filial
from apps.estoque.models import Estoque, LoteProduto
from apps.estoque.services.analise_estoque import classificar_abc_giro
from apps.estoque.services.cobertura_service import CLASSE_LABEL, carregar_faixas, classificar_cobertura, resolver_faixa
from apps.estoque.services.configuracao_abc_service import carregar_ajustes_abc, resolver_ajuste_abc
from apps.estoque.services.equilibrio_estoque import STATUS_VENDA_REALIZADA, _cobertura, _decimal
from apps.pdv.models import ItemVendaPDV
from apps.produtos.models import Produto, ProdutoFilial
from apps.vendas.models import ItemPedidoVenda

ZERO = Decimal("0")


def custo_unitario(produto) -> Decimal:
    return _decimal(produto.preco_custo_medio or produto.preco_custo)


def calcular_posicoes(*, empresa, dias_analise: int = 30, dias_cobertura: int = 14, filial_id=None) -> list[dict]:
    filiais = list(Filial.objects.filter(empresa=empresa, ativo=True))
    if filial_id:
        filiais = [filial for filial in filiais if filial.pk == filial_id]
    filial_ids = [filial.pk for filial in filiais]
    filial_por_id = {filial.pk: filial for filial in filiais}
    if not filial_ids:
        return []

    produtos = list(
        Produto.objects.for_empresa(empresa).filter(ativo=True)
        .select_related("unidade_medida", "categoria", "marca", "fornecedor")
    )
    produto_ids = [produto.pk for produto in produtos]

    vinculos = ProdutoFilial.objects.filter(
        produto_id__in=produto_ids, filial_id__in=filial_ids, ativo=True,
    ).values_list("produto_id", "filial_id")
    filiais_por_produto = defaultdict(list)
    for produto_id, filial_id_vinculo in vinculos:
        filiais_por_produto[produto_id].append(filial_id_vinculo)

    saldos = defaultdict(lambda: ZERO)
    for row in (
        Estoque.objects.filter(produto_id__in=produto_ids, filial_id__in=filial_ids)
        .values("produto_id", "filial_id")
        .annotate(total=Sum("quantidade_disponivel"))
    ):
        saldos[(row["produto_id"], row["filial_id"])] = _decimal(row["total"])

    inicio = timezone.now() - timezone.timedelta(days=dias_analise)
    vendas = defaultdict(lambda: ZERO)
    for row in (
        ItemVendaPDV.objects.filter(
            produto_id__in=produto_ids, venda_pdv__filial_id__in=filial_ids,
            venda_pdv__status="finalizada", venda_pdv__data_venda__gte=inicio,
        )
        .values("produto_id", "venda_pdv__filial_id")
        .annotate(total=Sum("quantidade"))
    ):
        vendas[(row["produto_id"], row["venda_pdv__filial_id"])] += _decimal(row["total"])
    for row in (
        ItemPedidoVenda.objects.filter(
            produto_id__in=produto_ids, pedido__filial_id__in=filial_ids,
            pedido__status__in=STATUS_VENDA_REALIZADA, pedido__data_emissao__gte=inicio,
        )
        .values("produto_id", "pedido__filial_id")
        .annotate(total=Sum("quantidade"))
    ):
        vendas[(row["produto_id"], row["pedido__filial_id"])] += _decimal(row["total"])

    produtos_com_lote_ids = [produto.pk for produto in produtos if produto.controla_lote]
    lotes_dias_vencer = {}
    if produtos_com_lote_ids:
        hoje = timezone.localdate()
        lotes_qs = LoteProduto.objects.filter(
            produto_id__in=produtos_com_lote_ids, filial_id__in=filial_ids,
            status=LoteProduto.Status.ATIVO, quantidade_atual__gt=ZERO,
            data_validade__isnull=False,
        ).filter(data_validade__gte=hoje)
        for lote in lotes_qs:
            chave = (lote.produto_id, lote.filial_id)
            dias = (lote.data_validade - hoje).days
            atual = lotes_dias_vencer.get(chave)
            if atual is None or dias < atual:
                lotes_dias_vencer[chave] = dias

    faixas = carregar_faixas(empresa=empresa)
    ajustes_abc = carregar_ajustes_abc(empresa=empresa)
    classe_abc_por_produto = {
        item["produto"].pk: item["classe"]
        for item in classificar_abc_giro(empresa=empresa, dias_analise=dias_analise)["itens"]
    }

    divisor = Decimal(dias_analise)
    posicoes = []
    for produto in produtos:
        vinculadas = filiais_por_produto.get(produto.pk, [])
        if not vinculadas:
            continue
        ajuste_abc = resolver_ajuste_abc(ajustes_abc, classe_abc_por_produto.get(produto.pk))
        dias_meta = max(0, max(dias_cobertura, produto.lead_time_reposicao_dias or 0) + ajuste_abc.dias_cobertura_extra)
        faixa = resolver_faixa(faixas, produto)
        custo = custo_unitario(produto)
        for filial_id_produto in vinculadas:
            vendido = vendas[(produto.pk, filial_id_produto)]
            demanda_diaria = vendido / divisor
            saldo = saldos[(produto.pk, filial_id_produto)]
            reserva = max(
                _decimal(produto.estoque_minimo) * ajuste_abc.multiplicador_minimo,
                _decimal(produto.estoque_seguranca),
                demanda_diaria * Decimal(dias_meta),
            )
            cobertura = _cobertura(saldo, demanda_diaria)
            classe = classificar_cobertura(cobertura, faixa)
            posicoes.append({
                "produto": produto,
                "filial": filial_por_id[filial_id_produto],
                "saldo": saldo,
                "vendido": vendido,
                "demanda_diaria": demanda_diaria,
                "cobertura": cobertura,
                "classe": classe,
                "classe_label": CLASSE_LABEL.get(classe, ""),
                "classe_abc": classe_abc_por_produto.get(produto.pk, ""),
                "meta": reserva,
                "excedente": max(ZERO, saldo - reserva),
                "deficit": max(ZERO, reserva - saldo),
                "custo_unitario": custo,
                "valor": (saldo * custo).quantize(Decimal("0.01")),
                "parado": vendido == ZERO,
                "lote_dias_vencer": lotes_dias_vencer.get((produto.pk, filial_id_produto)) if produto.controla_lote else None,
            })
    return posicoes
