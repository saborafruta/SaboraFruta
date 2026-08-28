"""
A mercadoria de um pedido sai do estoque UMA vez.

O DEFEITO QUE ISTO FECHA. Um pedido de venda tem hoje duas portas para o
mesmo movimento físico:

  · o FATURAMENTO (`VendaService.faturar_pedido`) baixa o estoque e libera a
    reserva;
  · a VIAGEM, ao fechar a carga, baixa o estoque de cada item — inclusive dos
    itens que vieram de um pedido de venda.

As duas portas existem por bons motivos e nenhuma delas está errada sozinha.
Juntas, elas tiravam a mesma mercadoria duas vezes: um pedido faturado e
depois carregado saía do estoque em dobro, e o razão ficava com dois
movimentos de saída para a mesma caixa. O estoque some, o custo médio se
desloca e a conferência do inventário culpa a contagem.

POR QUE NÃO BASTA ESCOLHER UMA DAS PORTAS. Faturar antes de carregar é comum
(o pedido faturado é carregável de propósito: nada marca pedido como
entregue, e ele fica esperando quem o leve). Carregar antes de faturar também
é comum, porque o caminhão sai de madrugada e a nota é emitida depois. As
duas ordens acontecem na mesma semana na mesma empresa.

ENTÃO A REGRA É POR QUANTIDADE, NÃO POR PORTA: cada porta pergunta quanto
daquele pedido e daquele produto JÁ saiu, e baixa apenas a diferença. Quem
chega primeiro baixa; quem chega depois não repete. Parcial funciona pelo
mesmo caminho, sem caso especial — metade faturada e metade carregada somam
uma saída só.

AS DUAS FONTES SÃO LIDAS DE ONDE ELAS SÃO VERDADE:

  · o faturamento deixa rastro no RAZÃO, com o documento do pedido;
  · a viagem deixa rastro na CARGA, e o razão dela aponta para a viagem, não
    para o pedido. Somar o razão da viagem exigiria adivinhar qual pedido
    pagou qual movimento quando dois pedidos levam o mesmo produto no mesmo
    caminhão -- por isso a carga é lida pelos itens dela, que sabem o pedido.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from apps.estoque.models import MovimentacaoEstoque

ZERO = Decimal('0')

SAIDAS = (
    MovimentacaoEstoque.TipoOperacao.SAIDA,
    MovimentacaoEstoque.TipoOperacao.TRANSFERENCIA_SAIDA,
)


class SaidaUnicaService:

    @staticmethod
    def baixado_no_faturamento(filial_id, pedido_id, produto_id) -> Decimal:
        """Quanto o faturamento deste pedido já tirou do estoque."""
        total = (
            MovimentacaoEstoque.objects
            .filter(
                filial_id=filial_id,
                produto_id=produto_id,
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.PEDIDO_VENDA,
                documento_id=pedido_id,
                tipo_operacao__in=SAIDAS,
            )
            .aggregate(total=Sum('quantidade'))['total']
        )
        return total or ZERO

    @staticmethod
    def embarcado_em_viagem(pedido_id, produto_id, ignorar_viagem=None) -> Decimal:
        """
        Quanto deste pedido já subiu num caminhão que fechou a carga.

        Só viagem que FECHOU conta: enquanto ela está em planejamento nada
        saiu do estoque, e descontar uma carga que ainda pode ser desmontada
        deixaria o faturamento sem baixar coisa nenhuma.

        `ignorar_viagem` existe para a própria viagem que está fechando agora
        não descontar a si mesma.
        """
        from apps.logistica.models import ItemCarga, Viagem

        itens = (
            ItemCarga.objects
            .filter(pedido_venda_id=pedido_id, produto_id=produto_id)
            .exclude(viagem__status__in=(
                Viagem.Status.RASCUNHO,
                Viagem.Status.EM_PREPARACAO,
                Viagem.Status.CANCELADA,
            ))
        )
        if ignorar_viagem is not None:
            itens = itens.exclude(viagem_id=getattr(ignorar_viagem, 'pk', ignorar_viagem))
        return itens.aggregate(total=Sum('quantidade'))['total'] or ZERO

    @classmethod
    def ja_saiu(cls, filial_id, pedido_id, produto_id, ignorar_viagem=None) -> Decimal:
        """As duas portas somadas."""
        return (
            cls.baixado_no_faturamento(filial_id, pedido_id, produto_id)
            + cls.embarcado_em_viagem(pedido_id, produto_id, ignorar_viagem)
        )

    @classmethod
    def a_baixar(cls, filial_id, pedido_id, produto_id, quantidade,
                 ignorar_viagem=None) -> Decimal:
        """
        Quanto ainda falta tirar do estoque para este item.

        Nunca negativo: se já saiu mais do que este item pede, a resposta é
        zero — devolver negativo faria a porta seguinte DEVOLVER mercadoria ao
        estoque, que é um jeito novo de errar o mesmo número.
        """
        pedido = Decimal(str(quantidade or 0))
        falta = pedido - cls.ja_saiu(filial_id, pedido_id, produto_id, ignorar_viagem)
        return falta if falta > ZERO else ZERO
