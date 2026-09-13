"""Sugestoes de transferencia para equilibrar estoque entre filiais."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN

from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.compras.models import ItemPedidoCompra, PedidoCompra
from apps.core.models import Filial
from apps.estoque.models import Estoque
from apps.pdv.models import ItemVendaPDV
from apps.produtos.models import Produto, ProdutoFilial
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


ZERO = Decimal("0")

# Pedido de venda so' vira demanda de verdade quando a mercadoria de fato
# saiu (faturado/entregue) -- rascunho, aprovacao e separacao ainda podem
# cair, e contar isso infla a demanda diaria com venda que nunca aconteceu.
STATUS_VENDA_REALIZADA = (
    PedidoVenda.Status.FATURADO,
    PedidoVenda.Status.PARCIALMENTE_FATURADO,
    PedidoVenda.Status.ENTREGUE,
)

# Pedido de compra "em aberto" e' qualquer um que ainda vai trazer
# mercadoria -- os dois status finais (recebido/cancelado) sao os unicos
# que nao tem mais nada a caminho.
#
# TRANSFERENCIA ENTRE LOJAS NAO ENTRA AQUI DE PROPOSITO:
# `MovimentacaoService.transferir_entre_filiais` credita o destino na
# hora (a entrada é criada junto com a saída, na mesma chamada) -- a
# conferência posterior é auditoria de quanto chegou, não o gatilho que
# faz a mercadoria aparecer no saldo. Uma transferência "aguardando
# conferência" já está dentro de `Estoque.quantidade_disponivel` do
# destino; somar de novo aqui contaria a mesma mercadoria duas vezes.
STATUS_COMPRA_ENCERRADA = (PedidoCompra.Status.RECEBIDO, PedidoCompra.Status.CANCELADO)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _quantidade(produto: Produto, value: Decimal) -> Decimal:
    casas = Decimal("0.001") if (produto.fracionavel or produto.vendido_por_peso_granel) else Decimal("1")
    return max(ZERO, value).quantize(casas, rounding=ROUND_DOWN)


def _cobertura(saldo: Decimal, demanda_diaria: Decimal):
    if demanda_diaria <= ZERO:
        return None
    return max(ZERO, saldo / demanda_diaria).quantize(Decimal("0.1"))


def calcular_equilibrio(
    *, empresa, dias_analise: int = 30, dias_cobertura: int = 14,
    busca: str = "", filial_origem_id=None, filial_destino_id=None,
) -> dict:
    """Calcula propostas; nunca altera estoque nem cria movimentacoes."""
    filiais = list(
        Filial.objects.filter(empresa=empresa, ativo=True)
        .order_by("-is_matriz", "nome_fantasia", "razao_social")
    )
    filial_ids = [filial.pk for filial in filiais]
    filial_por_id = {filial.pk: filial for filial in filiais}
    if len(filiais) < 2:
        return {"filiais": filiais, "sugestoes": [], "produtos_analisados": 0}

    produtos_qs = Produto.objects.for_empresa(empresa).filter(ativo=True)
    if busca:
        produtos_qs = produtos_qs.filter(
            Q(descricao__icontains=busca)
            | Q(codigo__icontains=busca)
            | Q(codigo_barras__icontains=busca)
        )
    produtos = list(produtos_qs.select_related("unidade_medida").order_by("descricao"))
    produto_ids = [produto.pk for produto in produtos]

    vinculos = ProdutoFilial.objects.filter(
        produto_id__in=produto_ids, filial_id__in=filial_ids, ativo=True,
    ).values_list("produto_id", "filial_id")
    filiais_por_produto = defaultdict(list)
    for produto_id, filial_id in vinculos:
        filiais_por_produto[produto_id].append(filial_id)

    saldos = defaultdict(lambda: ZERO)
    for row in (
        Estoque.objects.filter(produto_id__in=produto_ids, filial_id__in=filial_ids)
        .values("produto_id", "filial_id")
        .annotate(total=Sum("quantidade_disponivel"))
    ):
        saldos[(row["produto_id"], row["filial_id"])] = _decimal(row["total"])

    inicio = timezone.now() - timezone.timedelta(days=dias_analise)
    # Demanda soma os dois canais de venda -- PDV (balcao) e pedido de venda
    # (B2B/atacado). Ignorar o segundo faria uma filial que vende so' por
    # pedido parecer sem giro nenhum, e o equilibrio mandaria embora
    # justamente o estoque que ela mais precisa.
    vendas = defaultdict(lambda: ZERO)
    for row in (
        ItemVendaPDV.objects.filter(
            produto_id__in=produto_ids,
            venda_pdv__filial_id__in=filial_ids,
            venda_pdv__status="finalizada",
            venda_pdv__data_venda__gte=inicio,
        )
        .values("produto_id", "venda_pdv__filial_id")
        .annotate(total=Sum("quantidade"))
    ):
        vendas[(row["produto_id"], row["venda_pdv__filial_id"])] += _decimal(row["total"])
    for row in (
        ItemPedidoVenda.objects.filter(
            produto_id__in=produto_ids,
            pedido__filial_id__in=filial_ids,
            pedido__status__in=STATUS_VENDA_REALIZADA,
            pedido__data_emissao__gte=inicio,
        )
        .values("produto_id", "pedido__filial_id")
        .annotate(total=Sum("quantidade"))
    ):
        vendas[(row["produto_id"], row["pedido__filial_id"])] += _decimal(row["total"])

    # O que ja esta a caminho de cada filial via compra em aberto com o
    # fornecedor -- mercadoria que o sistema ainda nao contou em nenhum
    # saldo. Sem isso, o equilibrio sugeriria mandar de outra loja algo
    # que ja esta chegando pelo fornecedor.
    a_caminho = defaultdict(lambda: ZERO)
    for row in (
        ItemPedidoCompra.objects.filter(
            produto_id__in=produto_ids, pedido__filial_id__in=filial_ids,
        )
        .exclude(pedido__status__in=STATUS_COMPRA_ENCERRADA)
        .values("produto_id", "pedido__filial_id")
        .annotate(pendente=Sum(F("quantidade") - F("quantidade_recebida")))
    ):
        a_caminho[(row["produto_id"], row["pedido__filial_id"])] += _decimal(row["pendente"])

    sugestoes = []
    analisados = 0
    divisor = Decimal(dias_analise)
    for produto in produtos:
        vinculadas = filiais_por_produto.get(produto.pk, [])
        if len(vinculadas) < 2:
            continue
        analisados += 1
        posicoes = []
        for filial_id in vinculadas:
            vendido = vendas[(produto.pk, filial_id)]
            demanda_diaria = vendido / divisor
            saldo = saldos[(produto.pk, filial_id)]
            reserva = max(
                _decimal(produto.estoque_minimo),
                _decimal(produto.estoque_seguranca),
                demanda_diaria * Decimal(dias_cobertura),
            )
            posicoes.append({
                "filial_id": filial_id,
                "saldo": saldo,
                "vendido": vendido,
                "demanda_diaria": demanda_diaria,
                "reserva": reserva,
                "a_caminho": a_caminho[(produto.pk, filial_id)],
            })

        # Primeiro cada filial preserva sua reserva. Se a rede possui estoque
        # alem dessas reservas, o restante acompanha a participacao de cada
        # loja nas vendas do produto. Assim, mercadoria parada nao continua
        # concentrada onde nao gira enquanto falta onde vende.
        estoque_rede = sum((item["saldo"] for item in posicoes), ZERO)
        reserva_rede = sum((item["reserva"] for item in posicoes), ZERO)
        demanda_rede = sum((item["demanda_diaria"] for item in posicoes), ZERO)
        excedente_rede = max(ZERO, estoque_rede - reserva_rede)
        for item in posicoes:
            adicional = (
                excedente_rede * item["demanda_diaria"] / demanda_rede
                if excedente_rede > ZERO and demanda_rede > ZERO else ZERO
            )
            item["meta"] = item["reserva"] + adicional
            # O que ja esta a caminho cobre parte (ou tudo) do deficit antes
            # de qualquer sugestao nova -- nao conta pro excedente, porque
            # essa mercadoria ainda nao chegou e nao pode ser reenviada.
            item["deficit"] = max(ZERO, item["meta"] - item["saldo"] - item["a_caminho"])
            item["excedente"] = max(ZERO, item["saldo"] - item["meta"])

        destinos = sorted(
            (item for item in posicoes if item["deficit"] > ZERO),
            key=lambda item: (item["deficit"], item["demanda_diaria"]), reverse=True,
        )
        origens = sorted(
            (item for item in posicoes if item["excedente"] > ZERO),
            key=lambda item: (item["vendido"] == ZERO, item["excedente"]), reverse=True,
        )
        for destino in destinos:
            restante = destino["deficit"]
            for origem in origens:
                if restante <= ZERO or origem["excedente"] <= ZERO:
                    break
                if filial_origem_id and origem["filial_id"] != filial_origem_id:
                    continue
                if filial_destino_id and destino["filial_id"] != filial_destino_id:
                    continue
                quantidade = _quantidade(produto, min(restante, origem["excedente"]))
                if quantidade <= ZERO:
                    continue
                origem["excedente"] -= quantidade
                restante -= quantidade
                origem_filial = filial_por_id[origem["filial_id"]]
                destino_filial = filial_por_id[destino["filial_id"]]
                sugestoes.append({
                    "produto": produto,
                    "origem": origem_filial,
                    "destino": destino_filial,
                    "quantidade": quantidade,
                    "origem_saldo": origem["saldo"],
                    "destino_saldo": destino["saldo"],
                    "origem_vendido": origem["vendido"],
                    "destino_vendido": destino["vendido"],
                    "origem_cobertura": _cobertura(origem["saldo"], origem["demanda_diaria"]),
                    "destino_cobertura": _cobertura(destino["saldo"], destino["demanda_diaria"]),
                    "destino_meta": destino["meta"].quantize(Decimal("0.001")),
                    "destino_a_caminho": destino["a_caminho"],
                    "produto_parado_origem": origem["vendido"] == ZERO,
                    "destino_sem_estoque": destino["saldo"] <= ZERO,
                })

    sugestoes.sort(key=lambda item: (
        not item["destino_sem_estoque"],
        not item["produto_parado_origem"],
        -item["quantidade"],
        item["produto"].descricao,
    ))
    return {"filiais": filiais, "sugestoes": sugestoes, "produtos_analisados": analisados}
