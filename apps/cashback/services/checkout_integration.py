"""
Pontos de integração do cashback com o checkout do PDV.

Mantidos como funções finas e isoladas (em vez de espalhar chamadas ao
`CashbackWalletService` direto pelo `venda_pdv_service.py`) para que a
regra de "falha no cashback nunca derruba a venda" fique centralizada
num único lugar.
"""
from __future__ import annotations

import logging

from .wallet_service import CashbackWalletService

logger = logging.getLogger(__name__)


def creditar_pos_venda(venda, usuario=None, request=None) -> None:
    """
    Credita o cashback gerado pela venda recém-finalizada.

    A venda já foi concluída quando isso é chamado (cupom já pode ser
    emitido) — por isso qualquer erro aqui é só logado, nunca propagado,
    para não derrubar uma venda que já aconteceu de verdade por causa de
    um problema no cálculo/crédito de cashback.
    """
    try:
        CashbackWalletService.creditar(venda=venda, usuario=usuario, request=request)
    except Exception:
        logger.exception("Falha ao creditar cashback da venda #%s", venda.pk)


def debitar_no_checkout(*, venda, valor, usuario=None, request=None):
    """
    Debita cashback como forma de pagamento no checkout. Diferente do
    crédito pós-venda, erros aqui DEVEM propagar — o cliente não pode
    pagar com saldo que não existe, e a venda ainda está sendo montada
    (dentro da mesma transação atômica de `finalizar_venda`).
    """
    return CashbackWalletService.debitar(
        cliente=venda.cliente,
        empresa=venda.filial.empresa,
        valor=valor,
        venda=venda,
        usuario=usuario,
        request=request,
    )
