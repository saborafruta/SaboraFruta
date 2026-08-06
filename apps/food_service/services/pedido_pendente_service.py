from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.food_service.models import (
    Comanda,
    ComplementoItemPedidoPendente,
    ItemPedidoPendente,
    Mesa,
    PedidoPendente,
)
from apps.food_service.services.comanda_service import ComandaService
from apps.produtos.models import Produto


class PedidoPendenteService:
    """
    Pedido feito pelo cliente no Cardápio Digital -- fica em staging até um
    garçom confirmar. Nada aqui toca em estoque, preço ou pagamento; a única
    escrita "operacional" é abrir uma comanda vazia quando a mesa ainda não
    tem uma (seguro: `ComandaService.abrir` não mexe em item/estoque).
    """

    @classmethod
    @transaction.atomic
    def criar_pedido_pendente(cls, *, mesa: Mesa, itens: list[dict]) -> PedidoPendente:
        if not itens:
            raise DadosInvalidosError('Carrinho vazio.')

        produtos_validos = Produto.objects.for_filial(mesa.filial).filter(ativo=True)
        itens_normalizados = []
        for item_dados in itens:
            try:
                produto_id = int(item_dados.get('produto_id'))
                quantidade = Decimal(str(item_dados.get('quantidade', '1')))
            except (TypeError, ValueError, ArithmeticError):
                raise DadosInvalidosError('Item de pedido inválido.')
            if quantidade <= 0:
                raise DadosInvalidosError('Quantidade deve ser positiva.')
            if not produtos_validos.filter(pk=produto_id).exists():
                raise DadosInvalidosError('Produto indisponível no momento.')

            complementos_normalizados = []
            for complemento_dados in item_dados.get('complementos', []) or []:
                try:
                    complemento_produto_id = int(complemento_dados.get('produto_id'))
                    complemento_quantidade = Decimal(str(complemento_dados.get('quantidade', '1')))
                except (TypeError, ValueError, ArithmeticError):
                    raise DadosInvalidosError('Complemento inválido.')
                if complemento_quantidade <= 0:
                    raise DadosInvalidosError('Quantidade de complemento deve ser positiva.')
                if not produtos_validos.filter(pk=complemento_produto_id).exists():
                    raise DadosInvalidosError('Complemento indisponível no momento.')
                complementos_normalizados.append({
                    'produto_id': complemento_produto_id,
                    'quantidade': complemento_quantidade,
                })

            itens_normalizados.append({
                'produto_id': produto_id,
                'quantidade': quantidade,
                'observacoes': (item_dados.get('observacoes') or '').strip()[:500],
                'complementos': complementos_normalizados,
            })

        comanda = (
            Comanda.objects.for_filial(mesa.filial)
            .filter(mesas=mesa, status=Comanda.Status.ABERTA)
            .first()
        )
        if not comanda:
            comanda = ComandaService.abrir(
                filial=mesa.filial,
                usuario=None,
                mesas=[mesa],
                tipo=Comanda.Tipo.QR_CODE,
            )

        pedido = PedidoPendente.objects.create(mesa=mesa, comanda=comanda)
        for item_dados in itens_normalizados:
            item = ItemPedidoPendente.objects.create(
                pedido=pedido,
                produto_id=item_dados['produto_id'],
                quantidade=item_dados['quantidade'],
                observacoes=item_dados['observacoes'],
            )
            for complemento_dados in item_dados['complementos']:
                ComplementoItemPedidoPendente.objects.create(
                    item=item,
                    produto_id=complemento_dados['produto_id'],
                    quantidade=complemento_dados['quantidade'],
                )
        return pedido

    @classmethod
    @transaction.atomic
    def confirmar_pedido(cls, *, pedido: PedidoPendente, usuario) -> None:
        if pedido.status != PedidoPendente.Status.PENDENTE:
            raise DadosInvalidosError('Pedido já foi processado.')
        if not pedido.comanda_id or pedido.comanda.status != Comanda.Status.ABERTA:
            raise DadosInvalidosError('A comanda deste pedido não está mais aberta.')

        for item in pedido.itens.select_related('produto').prefetch_related('complementos__produto'):
            item_comanda = ComandaService.adicionar_item(
                comanda=pedido.comanda,
                produto=item.produto,
                quantidade=item.quantidade,
                observacoes=item.observacoes,
            )
            for complemento in item.complementos.all():
                ComandaService.adicionar_complemento(
                    item=item_comanda,
                    produto=complemento.produto,
                    quantidade=complemento.quantidade,
                )

        pedido.status = PedidoPendente.Status.CONFIRMADO
        pedido.confirmado_em = timezone.now()
        pedido.confirmado_por = usuario
        pedido.save(update_fields=['status', 'confirmado_em', 'confirmado_por'])

    @classmethod
    def recusar_pedido(cls, *, pedido: PedidoPendente, motivo: str = '') -> None:
        if pedido.status != PedidoPendente.Status.PENDENTE:
            raise DadosInvalidosError('Pedido já foi processado.')
        pedido.status = PedidoPendente.Status.RECUSADO
        pedido.recusado_motivo = (motivo or '').strip()[:200]
        pedido.save(update_fields=['status', 'recusado_motivo'])
