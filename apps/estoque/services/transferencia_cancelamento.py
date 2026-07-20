"""
Cancelamento (estorno) e exclusão de transferências entre lojas.

"Cancelar" reverte o efeito no estoque criando uma transferência inversa
(destino -> origem) e marca as movimentações originais como canceladas,
preservando o histórico. "Excluir" (restrito a administradores) primeiro
cancela — se ainda não tiver sido — e depois apaga definitivamente todas
as movimentações relacionadas (originais e de estorno).

Regra de negócio: nenhuma das duas ações é permitida enquanto houver uma
NF-e AUTORIZADA vinculada à transferência — é preciso cancelar a nota
fiscal primeiro (apps.estoque.services.transferencia_nfe.cancelar_nfe_transferencia).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService
from apps.financeiro.constants.enums import StatusDocumentoFiscal


def _documento_estorno(documento_numero: str) -> str:
    return f'EST-{documento_numero}'[:20]


def _movs_saida_transferencia(documento_numero: str, filial_origem):
    return list(
        MovimentacaoEstoque.objects
        .select_for_update()
        .filter(
            filial=filial_origem,
            tipo_operacao=MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            documento_numero=documento_numero,
        )
        .select_related('lote', 'filial_destino', 'documento_fiscal', 'produto')
    )


def _bloquear_se_nota_autorizada(movs_saida, acao: str) -> None:
    tem_nota_ativa = any(
        m.documento_fiscal_id and m.documento_fiscal.status == StatusDocumentoFiscal.AUTORIZADA
        for m in movs_saida
    )
    if tem_nota_ativa:
        raise DadosInvalidosError(
            f'Existe uma NF-e autorizada para esta transferência. '
            f'Cancele a NF-e antes de {acao}.'
        )


@transaction.atomic
def cancelar_transferencia(documento_numero: str, filial_origem, usuario) -> list[MovimentacaoEstoque]:
    """
    Estorna a transferência: devolve a quantidade para a filial de origem e
    remove da filial de destino (transferência inversa), sem apagar o
    histórico original — apenas marcando-o como cancelado.
    """
    movs_saida = _movs_saida_transferencia(documento_numero, filial_origem)
    if not movs_saida:
        raise DadosInvalidosError('Transferência não encontrada.')
    if all(m.transferencia_cancelada for m in movs_saida):
        raise DadosInvalidosError('Esta transferência já foi cancelada.')

    _bloquear_se_nota_autorizada(movs_saida, 'cancelar a transferência')

    filial_destino = movs_saida[0].filial_destino
    if not filial_destino:
        raise DadosInvalidosError('Filial de destino não identificada nesta transferência.')

    documento_estorno = _documento_estorno(documento_numero)
    movs_reversao: list[MovimentacaoEstoque] = []
    for mov in movs_saida:
        lote_destino_id = None
        if mov.lote_id:
            lote_destino = LoteProduto.objects.filter(
                produto_id=mov.produto_id,
                filial_id=filial_destino.pk,
                numero_lote=mov.lote.numero_lote,
            ).first()
            lote_destino_id = lote_destino.pk if lote_destino else None

        mov_saida_estorno, mov_entrada_estorno = MovimentacaoService.transferir_entre_filiais(
            produto_id=mov.produto_id,
            filial_origem_id=filial_destino.pk,
            filial_destino_id=filial_origem.pk,
            quantidade=mov.quantidade,
            usuario_id=usuario.pk,
            lote_id=lote_destino_id,
            observacao=f'Estorno da transferência {documento_numero}.',
            permitir_sem_lote=True,
            vincular_destino=True,
            documento_numero=documento_estorno,
        )
        movs_reversao.extend([mov_saida_estorno, mov_entrada_estorno])

    agora = timezone.now()
    MovimentacaoEstoque.objects.filter(
        documento_numero=documento_numero,
        tipo_operacao__in=[
            MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
            MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_ENTRADA,
        ],
    ).update(
        transferencia_cancelada=True,
        transferencia_cancelada_em=agora,
        transferencia_cancelada_por=usuario,
    )

    return movs_reversao


@transaction.atomic
def excluir_transferencia(documento_numero: str, filial_origem, usuario) -> None:
    """
    Exclui definitivamente uma transferência. Restrito a administradores
    (verificação de permissão é responsabilidade da view). Se a
    transferência ainda não tiver sido cancelada, reverte o estoque
    primeiro; depois apaga todas as movimentações envolvidas.
    """
    movs_saida = _movs_saida_transferencia(documento_numero, filial_origem)
    if not movs_saida:
        raise DadosInvalidosError('Transferência não encontrada.')

    _bloquear_se_nota_autorizada(movs_saida, 'excluir a transferência')

    ja_cancelada = all(m.transferencia_cancelada for m in movs_saida)
    if not ja_cancelada:
        cancelar_transferencia(documento_numero, filial_origem, usuario)

    documento_estorno = _documento_estorno(documento_numero)
    MovimentacaoEstoque.objects.filter(
        documento_numero__in=[documento_numero, documento_estorno],
    ).delete()
