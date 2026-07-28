"""
Substituição de venda editada no PDV.

Quando o operador reabre uma venda antiga (recarrega os itens no
carrinho para corrigir algo) e finaliza de novo, isso não pode gerar
uma segunda venda ativa — a original precisa ser estornada (estoque
devolvido, conta a receber cancelada) antes da nova ser criada, para
não duplicar valores no caixa e nos recebimentos.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusContaReceber, StatusDocumentoFiscal
from apps.financeiro.models import ContaReceber
from apps.pdv.models import VendaPDV
from apps.pdv.services.cancelamento_fiscal_service import obter_documento_fiscal


def validar_venda_editavel(venda: VendaPDV) -> None:
    if venda.status != "finalizada":
        raise DadosInvalidosError(
            "Esta venda não pode ser editada (já está cancelada ou com outro status)."
        )
    documento = obter_documento_fiscal(venda)
    if documento and documento.status in (
        StatusDocumentoFiscal.AUTORIZADA,
        StatusDocumentoFiscal.PROCESSANDO,
    ):
        raise DadosInvalidosError(
            "Esta venda tem NF-e/NFC-e autorizada ou em processamento. "
            "Conclua ou cancele o documento fiscal antes de editar a venda."
        )


@transaction.atomic
def estornar_venda_para_edicao(venda: VendaPDV, usuario) -> None:
    """
    Reverte por completo uma venda (estoque + conta a receber vinculada)
    e a marca como cancelada. Chamado antes de recriar a versão editada,
    para que só a venda nova conte no caixa e nos recebimentos.
    """
    venda = VendaPDV.objects.select_for_update().get(pk=venda.pk)
    if venda.status != "finalizada":
        return  # já tratada (corrida ou clique duplo)

    for item in venda.itens.all():
        if not item.estoque_baixado or not item.movimentacoes_estoque_ids:
            continue
        movs = MovimentacaoEstoque.objects.filter(pk__in=item.movimentacoes_estoque_ids)
        for mov in movs:
            MovimentacaoService.registrar_movimentacao(
                produto_id=mov.produto_id,
                filial_id=mov.filial_id,
                tipo_operacao=MovimentacaoEstoque.TipoOperacao.DEVOLUCAO_CLIENTE,
                quantidade=mov.quantidade,
                usuario_id=usuario.pk,
                lote_id=mov.lote_id,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.OUTRAS,
                documento_id=venda.pk,
                documento_numero=str(venda.numero_venda),
                observacao=f"Estorno automático: venda #{venda.numero_venda} foi editada pelo operador.",
                permitir_sem_lote=True,
            )

    contas = ContaReceber.objects.filter(
        filial=venda.filial,
        documento_tipo="venda_pdv",
        documento_id=venda.pk,
        status__in=[
            StatusContaReceber.ABERTO,
            StatusContaReceber.VENCIDO,
            StatusContaReceber.NEGOCIADO,
        ],
    )
    for conta in contas:
        conta.status = StatusContaReceber.CANCELADO
        nota = f"Cancelada automaticamente: venda #{venda.numero_venda} foi editada."
        conta.observacao = f"{conta.observacao} {nota}".strip() if conta.observacao else nota
        conta.save(update_fields=["status", "observacao", "updated_at"])

    if venda.sessao_pdv_id:
        # Doação/Permuta (movimenta_caixa=False) nunca entraram no total do
        # caixa — só descontamos a parte que de fato foi contabilizada.
        valor_nao_contabilizado = sum(
            (pg.valor - pg.troco) for pg in venda.pagamentos.select_related("forma_pagamento")
            if not pg.forma_pagamento.movimenta_caixa
        ) or 0
        valor_contabilizado = max(0, (venda.valor_total or 0) - valor_nao_contabilizado)
        sessao = venda.sessao_pdv
        novo_total = (sessao.total_vendas or 0) - valor_contabilizado
        sessao.total_vendas = novo_total if novo_total > 0 else 0
        sessao.save(update_fields=["total_vendas"])

    try:
        from apps.cashback.services.wallet_service import CashbackWalletService
        CashbackWalletService.estornar_venda(
            venda, usuario, "Venda editada pelo operador (substituída por uma nova versão)."
        )
    except Exception:
        logger.exception("Falha ao estornar cashback da venda #%s na edição", venda.pk)

    venda.status = "cancelada"
    venda.motivo_cancelamento = "Venda editada pelo operador (substituída por uma nova versão)."
    venda.cancelado_em = timezone.now()
    venda.cancelado_por = usuario
    venda.save(update_fields=[
        "status", "motivo_cancelamento", "cancelado_em", "cancelado_por", "updated_at",
    ])
