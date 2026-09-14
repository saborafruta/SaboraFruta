"""Sugestoes de transferencia para equilibrar estoque entre filiais."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_DOWN

from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.compras.models import ItemPedidoCompra, PedidoCompra
from apps.core.models import Filial
from apps.estoque.models import Estoque, LoteProduto
from apps.estoque.services.analise_estoque import classificar_abc_giro
from apps.estoque.services.cobertura_service import CLASSE_LABEL, carregar_faixas, classificar_cobertura, resolver_faixa
from apps.estoque.services.configuracao_abc_service import carregar_ajustes_abc, resolver_ajuste_abc
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

# Pedido de venda ainda em curso (nao caiu, nao faturou): sinaliza demanda
# que ja esta represada esperando estoque, mesmo sem ter virado venda.
STATUS_VENDA_PENDENTE = (
    PedidoVenda.Status.AGUARDANDO_APROVACAO,
    PedidoVenda.Status.APROVADO,
    PedidoVenda.Status.CONFIRMADO,
    PedidoVenda.Status.EM_SEPARACAO,
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


def _score_prioridade(
    *, destino, origem, produto, media_demanda_diaria, tem_pedido_pendente, classe_abc, lote_dias_vencer,
) -> int:
    """
    Score 0-100 que ordena as sugestoes pela combinacao de fatores de
    risco, nao so' pelo tamanho da transferencia. Pontos somam e o total
    e' limitado em 100 -- varios fatores de risco juntos nao inflam alem
    do teto.
    """
    pontos = 0
    if destino["classe"] == "ruptura":
        pontos += 30
    if destino["saldo"] < _decimal(produto.estoque_minimo):
        pontos += 25
    if media_demanda_diaria > ZERO and destino["demanda_diaria"] > media_demanda_diaria:
        pontos += 20
    if destino["cobertura"] is not None and destino["cobertura"] < Decimal("3"):
        pontos += 15
    if tem_pedido_pendente:
        pontos += 10
    if classe_abc == "A":
        pontos += 10
    if origem["vendido"] == ZERO:
        pontos += 10
    if produto.controla_lote and lote_dias_vencer is not None and lote_dias_vencer <= (produto.dias_aviso_vencimento or 0):
        pontos += 5
    # Compra em aberto ja' cobrindo parte do deficit desta filial: quanto
    # mais dela resolvida pela compra, menor a urgencia de tambem puxar
    # de outra filial -- ate' 15 pontos a menos, proporcional a fatia
    # coberta (se a compra sozinha ja resolvesse tudo, nao haveria
    # sugestao aqui pra comecar, entao a fatia nunca chega a 100%).
    if destino["deficit_bruto"] > ZERO and destino["a_caminho"] > ZERO:
        fatia_coberta = min(destino["a_caminho"], destino["deficit_bruto"]) / destino["deficit_bruto"]
        pontos -= int((fatia_coberta * Decimal("15")).to_integral_value())
    return max(0, min(100, pontos))


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
    produtos = list(produtos_qs.select_related("unidade_medida", "categoria").order_by("descricao"))
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

    # Pedido de venda ainda aberto (nao faturado) por filial/produto -- sinal
    # de demanda ja represada, usado no score de prioridade.
    pedidos_pendentes = defaultdict(lambda: ZERO)
    for row in (
        ItemPedidoVenda.objects.filter(
            produto_id__in=produto_ids,
            pedido__filial_id__in=filial_ids,
            pedido__status__in=STATUS_VENDA_PENDENTE,
        )
        .values("produto_id", "pedido__filial_id")
        .annotate(total=Sum("quantidade"))
    ):
        pedidos_pendentes[(row["produto_id"], row["pedido__filial_id"])] += _decimal(row["total"])

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

    # Data mais proxima entre as compras em aberto -- usada pra mostrar
    # "chega em X" na sugestao e pra pesar a prioridade (compra que chega
    # logo pesa mais contra a transferencia do que uma sem previsao).
    previsao_recebimento = {}
    for produto_id, filial_id_compra, data_prevista in (
        ItemPedidoCompra.objects.filter(
            produto_id__in=produto_ids, pedido__filial_id__in=filial_ids,
            pedido__data_entrega_prevista__isnull=False,
        )
        .exclude(pedido__status__in=STATUS_COMPRA_ENCERRADA)
        .values_list("produto_id", "pedido__filial_id", "pedido__data_entrega_prevista")
    ):
        chave = (produto_id, filial_id_compra)
        atual = previsao_recebimento.get(chave)
        if atual is None or data_prevista < atual:
            previsao_recebimento[chave] = data_prevista

    # Produtos com controle de lote/validade: soma so' o que esta em lote
    # ATIVO e nao vencido (o que pode de fato sair pra outra filial) e o
    # menor "dias para vencer" entre esses lotes (usado no score -- lote
    # perto do fim da validade pesa a favor de tirar da origem antes que
    # estrague parado).
    produtos_com_lote_ids = [produto.pk for produto in produtos if produto.controla_lote]
    lotes_disponiveis = defaultdict(lambda: ZERO)
    lotes_dias_vencer = {}
    if produtos_com_lote_ids:
        hoje = timezone.localdate()
        lotes_qs = LoteProduto.objects.filter(
            produto_id__in=produtos_com_lote_ids, filial_id__in=filial_ids,
            status=LoteProduto.Status.ATIVO, quantidade_atual__gt=ZERO,
        ).filter(Q(data_validade__isnull=True) | Q(data_validade__gte=hoje))
        for lote in lotes_qs:
            chave = (lote.produto_id, lote.filial_id)
            lotes_disponiveis[chave] += _decimal(lote.quantidade_atual)
            if lote.data_validade:
                dias = (lote.data_validade - hoje).days
                atual = lotes_dias_vencer.get(chave)
                if atual is None or dias < atual:
                    lotes_dias_vencer[chave] = dias

    faixas_cobertura = carregar_faixas(empresa=empresa)
    ajustes_abc = carregar_ajustes_abc(empresa=empresa)
    classe_abc_por_produto = {
        item["produto"].pk: item["classe"]
        for item in classificar_abc_giro(empresa=empresa, dias_analise=dias_analise)["itens"]
    }

    sugestoes = []
    analisados = 0
    divisor = Decimal(dias_analise)
    for produto in produtos:
        vinculadas = filiais_por_produto.get(produto.pk, [])
        if len(vinculadas) < 2:
            continue
        analisados += 1
        posicoes = []
        # A meta de cobertura nunca fica menor que o lead time de reposição
        # do produto: escolher "14 dias" no filtro não pode deixar uma loja
        # descoberta se o fornecedor dela demora 20 -- a reserva existe
        # justamente para o intervalo até a próxima compra chegar.
        #
        # Curva A gira mais e tolera menos ruptura que C -- o ajuste por
        # classe soma/tira dias da meta e escala o mínimo/máximo cadastrado
        # do produto. Sem ajuste configurado pra classe, e' identidade
        # (multiplicador 1, zero dias extra): comportamento de hoje.
        ajuste_abc = resolver_ajuste_abc(ajustes_abc, classe_abc_por_produto.get(produto.pk))
        dias_meta = max(0, max(dias_cobertura, produto.lead_time_reposicao_dias or 0) + ajuste_abc.dias_cobertura_extra)
        faixa = resolver_faixa(faixas_cobertura, produto)
        for filial_id in vinculadas:
            vendido = vendas[(produto.pk, filial_id)]
            demanda_diaria = vendido / divisor
            saldo = saldos[(produto.pk, filial_id)]
            reserva = max(
                _decimal(produto.estoque_minimo) * ajuste_abc.multiplicador_minimo,
                _decimal(produto.estoque_seguranca),
                demanda_diaria * Decimal(dias_meta),
            )
            cobertura = _cobertura(saldo, demanda_diaria)
            # Sem lote controlado, tudo que esta no saldo pode ser oferecido
            # pra transferencia. Com lote, so' o que esta em lote ativo e
            # nao vencido sai de verdade -- o resto ja esta reservado pro
            # consumo local ou parado esperando baixa/descarte.
            saldo_transferivel = saldo
            if produto.controla_lote:
                saldo_transferivel = min(saldo, lotes_disponiveis[(produto.pk, filial_id)])
            posicoes.append({
                "filial_id": filial_id,
                "saldo": saldo,
                "saldo_transferivel": saldo_transferivel,
                "vendido": vendido,
                "demanda_diaria": demanda_diaria,
                "reserva": reserva,
                "a_caminho": a_caminho[(produto.pk, filial_id)],
                "previsao_recebimento": previsao_recebimento.get((produto.pk, filial_id)),
                "cobertura": cobertura,
            })

        # Primeiro cada filial preserva sua reserva. Se a rede possui estoque
        # alem dessas reservas, o restante acompanha a participacao de cada
        # loja nas vendas do produto. Assim, mercadoria parada nao continua
        # concentrada onde nao gira enquanto falta onde vende.
        estoque_rede = sum((item["saldo"] for item in posicoes), ZERO)
        reserva_rede = sum((item["reserva"] for item in posicoes), ZERO)
        demanda_rede = sum((item["demanda_diaria"] for item in posicoes), ZERO)
        excedente_rede = max(ZERO, estoque_rede - reserva_rede)
        media_demanda_diaria = demanda_rede / len(posicoes) if posicoes else ZERO
        maximo = _decimal(produto.estoque_maximo) * ajuste_abc.multiplicador_maximo
        for item in posicoes:
            adicional = (
                excedente_rede * item["demanda_diaria"] / demanda_rede
                if excedente_rede > ZERO and demanda_rede > ZERO else ZERO
            )
            meta = item["reserva"] + adicional
            # O destino nunca precisa de mais do que comporta -- sem esse
            # teto, uma filial com pouca reserva mas muita venda podia
            # herdar excedente da rede alem do proprio maximo cadastrado.
            if maximo > ZERO:
                meta = min(meta, maximo)
            item["meta"] = meta
            # O que ja esta a caminho cobre parte (ou tudo) do deficit antes
            # de qualquer sugestao nova -- nao conta pro excedente, porque
            # essa mercadoria ainda nao chegou e nao pode ser reenviada.
            # Guarda o deficit ANTES de descontar a_caminho pra medir, no
            # score, o quanto a compra em aberto ja resolveu sozinha.
            item["deficit_bruto"] = max(ZERO, item["meta"] - item["saldo"])
            item["deficit"] = max(ZERO, item["deficit_bruto"] - item["a_caminho"])
            item["excedente"] = max(ZERO, item["saldo_transferivel"] - item["meta"])
            item["classe"] = classificar_cobertura(item["cobertura"], faixa)

        # Prioridade por URGÊNCIA (dias até faltar), não por tamanho do
        # déficit em unidades. Uma filial que vende pouco mas está a 1 dia
        # de ruptura corre mais risco do que uma que vende muito e ainda
        # tem uma semana de saldo -- atender pelo déficit bruto atenderia
        # primeiro quem menos precisa com urgência. Sem giro (sem venda no
        # período) não tem "dias até faltar" para medir; entra por último,
        # depois de quem realmente corre risco de parar de vender.
        def _urgencia(item):
            return (item["cobertura"] is None, item["cobertura"] if item["cobertura"] is not None else ZERO)

        destinos = sorted(
            (item for item in posicoes if item["deficit"] > ZERO),
            key=_urgencia,
        )
        origens = sorted(
            (item for item in posicoes if item["excedente"] > ZERO),
            key=lambda item: (item["vendido"] == ZERO, item["excedente"]), reverse=True,
        )
        for destino in destinos:
            restante = destino["deficit"]
            tem_pedido_pendente = pedidos_pendentes[(produto.pk, destino["filial_id"])] > ZERO
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
                lote_dias_vencer = lotes_dias_vencer.get((produto.pk, origem["filial_id"]))
                score = _score_prioridade(
                    destino=destino, origem=origem, produto=produto,
                    media_demanda_diaria=media_demanda_diaria,
                    tem_pedido_pendente=tem_pedido_pendente,
                    classe_abc=classe_abc_por_produto.get(produto.pk),
                    lote_dias_vencer=lote_dias_vencer,
                )
                sugestoes.append({
                    "produto": produto,
                    "origem": origem_filial,
                    "destino": destino_filial,
                    "quantidade": quantidade,
                    "origem_saldo": origem["saldo"],
                    "destino_saldo": destino["saldo"],
                    "origem_vendido": origem["vendido"],
                    "destino_vendido": destino["vendido"],
                    "origem_cobertura": origem["cobertura"],
                    "destino_cobertura": destino["cobertura"],
                    "origem_classe": origem["classe"],
                    "origem_classe_label": CLASSE_LABEL.get(origem["classe"], ""),
                    "destino_classe": destino["classe"],
                    "destino_classe_label": CLASSE_LABEL.get(destino["classe"], ""),
                    "destino_meta": destino["meta"].quantize(Decimal("0.001")),
                    "destino_a_caminho": destino["a_caminho"],
                    "destino_previsao_recebimento": destino["previsao_recebimento"],
                    "dias_meta": dias_meta,
                    "lead_time_maior_que_cobertura": produto.lead_time_reposicao_dias > dias_cobertura,
                    "produto_parado_origem": origem["vendido"] == ZERO,
                    "destino_sem_estoque": destino["saldo"] <= ZERO,
                    "destino_pedido_pendente": tem_pedido_pendente,
                    "origem_lote_dias_vencer": lote_dias_vencer,
                    "alerta_fefo": (
                        f"Produto próximo do vencimento na {origem_filial.nome_fantasia or origem_filial.razao_social} "
                        f"— avaliar transferência para {destino_filial.nome_fantasia or destino_filial.razao_social}."
                        if lote_dias_vencer is not None else ""
                    ),
                    "score": score,
                })

    sugestoes.sort(key=lambda item: (-item["score"], -item["quantidade"], item["produto"].descricao))
    return {"filiais": filiais, "sugestoes": sugestoes, "produtos_analisados": analisados}
