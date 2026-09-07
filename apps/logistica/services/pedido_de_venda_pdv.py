"""
Pedido de expedição a partir de uma venda do PDV com NF-e.

POR QUE PRECISA DISTO. O PDV atende balcão, mas às vezes o que sai dali
não é levado na hora: vira entrega, com NF-e emitida (não NFC-e) porque o
destinatário é identificado. Essa carga também precisa rodar num
caminhão -- e sem este vínculo, ela nunca chegava ao romaneio nem ao
MDF-e, porque o OMS só conhecia vendas do módulo Vendas
(`vendas.PedidoVenda`).

MANUAL, NÃO AUTOMÁTICO. Foi decisão do usuário: nem toda venda com NF-e
precisa de expedição (a maioria do PDV é balcão, sem entrega nenhuma), e
gerar pedido sozinho a cada emissão criaria lixo na fila. Quem decide
quais vendas viram pedido é quem opera esta tela, escolhendo entre as
elegíveis.

SÓ NF-e AUTORIZADA, NÃO IMPORTA SE MARCOU ENTREGA. A princípio só venda
com `delivery=True` precisaria de expedição -- o cliente de balcão já
levou a mercadoria na hora. Mas na prática uma venda de balcão para
pessoa jurídica também pode sair com NF-e (não NFC-e) por exigência
fiscal do comprador, e mesmo assim precisar ir de carga depois -- o
`delivery` e o tipo de documento fiscal são flags independentes (visto
em `apps/analytics/views/dashboards.py`, que filtra os dois
separadamente). Por isso o critério aqui é só "tem NF-e autorizada,
está finalizada e ainda não tem pedido" -- quem decide se aquela venda
específica precisa mesmo de expedição continua sendo o operador, ao
escolher na tela.
"""
from __future__ import annotations

from django.db import transaction

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models import DocumentoFiscal
from apps.pdv.models import VendaPDV

from ..models import ItemPedidoExpedicao, PedidoExpedicao


def _proximo_numero(filial) -> int:
    ultimo = (
        PedidoExpedicao.objects.for_filial(filial)
        .order_by("-numero").values_list("numero", flat=True).first()
    )
    return (ultimo or 0) + 1


def _ids_com_nfe_autorizada(filial):
    return DocumentoFiscal.objects.filter(
        filial=filial, origem_tipo="venda_pdv", tipo_documento="nfe",
        status=StatusDocumentoFiscal.AUTORIZADA,
    ).values_list("origem_id", flat=True)


def vendas_pdv_elegiveis(filial):
    """
    Vendas do PDV prontas para virar pedido de expedição: NF-e autorizada
    e que ainda não têm pedido nenhum -- independente de `delivery`, pois
    balcão para pessoa jurídica também pode sair com NF-e e precisar de
    carga depois (ver módulo).

    `distinct()` porque uma venda com NF-e reemitida (nova versão após
    cancelamento) pode aparecer mais de uma vez em `_ids_com_nfe_autorizada`
    -- o que importa aqui é SE tem, não quantas.
    """
    return (
        VendaPDV.objects.for_filial(filial)
        .filter(
            pk__in=list(_ids_com_nfe_autorizada(filial)),
            status="finalizada",
            pedidos_expedicao__isnull=True,
        )
        .select_related("cliente")
        .order_by("-data_venda")
        .distinct()
    )


@transaction.atomic
def gerar_pedido_expedicao(venda: VendaPDV, usuario) -> PedidoExpedicao:
    """
    Cria o Pedido de Expedição a partir da venda, já com os itens.

    NÃO GERA COBRANÇA NENHUMA -- a venda do PDV já foi paga (ou finalizada
    de outro jeito) no próprio PDV; um título aqui cobraria o cliente
    duas vezes. `forma_pagamento`/`condicao_pagamento` ficam em branco de
    propósito, mesmo comportamento que o pedido nascido de
    `vendas.PedidoVenda` já tem.
    """
    if venda.pedidos_expedicao.exists():
        raise DadosInvalidosError(
            f"A venda #{venda.numero_venda:06d} já tem pedido de expedição."
        )
    if not venda.cliente_id:
        raise DadosInvalidosError(
            "Venda sem cliente identificado — não dá para montar a entrega."
        )
    if not DocumentoFiscal.objects.filter(
        filial=venda.filial, origem_tipo="venda_pdv", origem_id=venda.pk,
        tipo_documento="nfe", status=StatusDocumentoFiscal.AUTORIZADA,
    ).exists():
        raise DadosInvalidosError(
            "Esta venda ainda não tem NF-e autorizada."
        )

    endereco = venda.endereco_entrega or {}
    pedido = PedidoExpedicao.objects.create(
        filial=venda.filial,
        numero=_proximo_numero(venda.filial),
        cliente=venda.cliente,
        venda_pdv=venda,
        responsavel=usuario,
        endereco_entrega={
            "cep": endereco.get("cep", ""),
            "endereco": endereco.get("rua", ""),
            "numero": endereco.get("numero", ""),
            "bairro": endereco.get("bairro", ""),
            "cidade": endereco.get("cidade", ""),
            "uf": endereco.get("uf", ""),
        },
        observacao=venda.observacao or "",
    )

    for ordem, item in enumerate(
        venda.itens.select_related("produto").order_by("numero_item"), start=1,
    ):
        produto = item.produto
        ItemPedidoExpedicao.objects.create(
            pedido=pedido,
            ordem=ordem * 10,
            produto_codigo=produto.codigo if produto else "",
            produto_nome=(produto.descricao_pdv or produto.descricao) if produto else "",
            quantidade=item.quantidade,
            unidade=item.unidade_medida or "UN",
            valor_unitario=item.valor_unitario,
        )
    pedido.recalcular_totais()
    return pedido
