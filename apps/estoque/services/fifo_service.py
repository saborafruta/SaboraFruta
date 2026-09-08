"""Servico FEFO/FIFO para separacao de lotes.

Este modulo apenas seleciona lotes e delega baixas ao MovimentacaoService.
"""
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.core.services.exceptions import EstoqueInsuficienteError
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.estoque.services.movimentacao_service import MovimentacaoService


class FIFOService:
    """Implementa FEFO: lotes com validade menor saem primeiro."""

    @staticmethod
    def separar(produto, filial, quantidade, permitir_sem_estoque=False, lote_preferencial=None):
        """Retorna lista de tuplas (lote, quantidade_a_baixar)."""
        quantidade = Decimal(quantidade)
        hoje = timezone.localdate()
        lotes_qs = (
            LoteProduto.objects
            .filter(
                produto=produto,
                filial=filial,
                status=LoteProduto.Status.ATIVO,
                quantidade_atual__gt=0,
            )
            .filter(Q(data_validade__isnull=True) | Q(data_validade__gte=hoje))
            .order_by(F('data_validade').asc(nulls_last=True), 'id')
        )

        lotes = list(lotes_qs)
        if lote_preferencial:
            preferencial = next((lote for lote in lotes if lote.id == lote_preferencial.id), None)
            if preferencial:
                lotes.remove(preferencial)
                lotes.insert(0, preferencial)

        plano = []
        restante = quantidade
        for lote in lotes:
            if restante <= 0:
                break
            disponivel = Decimal(lote.quantidade_atual)
            usar = min(disponivel, restante)
            plano.append((lote, usar))
            restante -= usar

        if restante > 0 and not permitir_sem_estoque:
            raise EstoqueInsuficienteError(
                f'Estoque insuficiente de {produto}: faltam {restante}.'
            )

        return plano

    @staticmethod
    @tenant_atomic
    def baixar(
        produto,
        filial,
        quantidade,
        usuario,
        tipo_operacao=MovimentacaoEstoque.TipoOperacao.SAIDA,
        documento_tipo='',
        documento_id=None,
        documento_numero='',
        observacao='',
        ordem_producao=None,
        lote_preferencial=None,
    ):
        """Separa via FEFO e registra as saidas pelo MovimentacaoService."""
        plano = FIFOService.separar(produto, filial, quantidade, lote_preferencial=lote_preferencial)
        movs = []
        for lote, qtd in plano:
            mov = MovimentacaoService.registrar_movimentacao(
                produto_id=produto.pk,
                filial_id=filial.pk,
                tipo_operacao=tipo_operacao,
                quantidade=qtd,
                usuario_id=usuario.pk,
                lote_id=lote.pk,
                valor_unitario=lote.custo_unitario,
                documento_tipo=documento_tipo,
                documento_id=documento_id or getattr(ordem_producao, 'pk', None),
                documento_numero=documento_numero,
                observacao=observacao,
            )
            movs.append(mov)
        return movs
