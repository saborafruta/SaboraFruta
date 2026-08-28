"""
Trazer para a expedição os itens que a venda já tem.

O QUE SE REDIGITAVA

O pedido de venda tem produto, quantidade, unidade e preço. A expedição
pedia tudo de novo, linha por linha, com a venda aberta em outra aba. Além
do tempo, cada linha redigitada é uma chance de sair quantidade diferente da
que foi vendida — e a diferença só aparece na conferência do cliente, quando
o caminhão já foi.

O QUE VEM É O SALDO, E NÃO A QUANTIDADE DO PEDIDO

Uma venda pode ser expedida em duas viagens: metade hoje, metade quando o
produto chegar. Trazer sempre a quantidade cheia faria a segunda expedição
repetir a primeira, e o cliente receberia o dobro do que comprou. Por isso o
serviço traz o que ainda falta expedir — quantidade da venda menos o que já
está em outras expedições vivas dela.

CADA LINHA SABE DE QUAL LINHA DA VENDA VEIO

O item da expedição guarda texto de propósito: é o que permite lançar carga
avulsa de algo que não está em pedido nenhum. Mas o que vem da venda aponta
para a linha de origem — sem isso, "quanto desta venda já foi expedido?" não
teria resposta, e clicar duas vezes duplicaria a carga.

O QUE ELE NÃO FAZ

Não mexe em estoque, não reserva e não fatura: trazer item para um pedido de
expedição é planejamento, e o que tira mercadoria do estoque é a viagem ou o
faturamento. Um serviço que baixasse aqui faria a mesma caixa sair duas
vezes.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from apps.core.services.exceptions import DadosInvalidosError
from apps.logistica.models import ItemPedidoExpedicao, PedidoExpedicao

ZERO = Decimal('0')

# Expedição que não conta mais como saída: o que estava nela voltou a ser
# saldo da venda.
MORTOS = (PedidoExpedicao.Status.CANCELADO,)


class ItensDaVendaService:

    # ── Leitura ──────────────────────────────────────────────────────────

    @classmethod
    def pendentes(cls, pedido) -> list[dict]:
        """
        O que da venda ainda falta expedir, linha a linha.

        LÊ AS OUTRAS EXPEDIÇÕES DA MESMA VENDA, e não só esta: a venda pode
        ter sido dividida em duas cargas, e o saldo é o da venda inteira.
        """
        venda = pedido.pedido_venda
        if venda is None:
            return []

        ja_nesta = cls._por_item(pedido.itens.all())
        ja_em_outras = cls._expedido_em_outras(venda, pedido)

        linhas = []
        for item in venda.itens.select_related('produto', 'produto__unidade_medida'):
            vendida = item.quantidade or ZERO
            expedida = ja_em_outras.get(item.pk, ZERO) + ja_nesta.get(item.pk, ZERO)
            linhas.append({
                'item': item,
                'produto': item.produto,
                'vendida': vendida,
                'expedida': expedida,
                'saldo': max(ZERO, vendida - expedida),
                'nesta': ja_nesta.get(item.pk, ZERO),
            })
        return linhas

    @classmethod
    def resumo(cls, pedido) -> dict:
        """O que a tela precisa para decidir se oferece o botão."""
        linhas = cls.pendentes(pedido)
        a_trazer = [l for l in linhas if l['saldo'] > ZERO]
        return {
            'linhas': linhas,
            'a_trazer': a_trazer,
            'tem_pendencia': bool(a_trazer),
            'ja_trouxe': any(l['nesta'] > ZERO for l in linhas),
            'venda': pedido.pedido_venda,
        }

    # ── Escrita ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def trazer(cls, pedido, usuario=None) -> dict:
        """
        Cria na expedição as linhas que faltam da venda.

        RECUSA QUANDO NÃO HÁ O QUE TRAZER, em vez de dizer que trouxe zero:
        "nada a trazer" e "trouxe tudo" são estados diferentes, e a mensagem
        precisa distinguir os dois.
        """
        if pedido.pedido_venda_id is None:
            raise DadosInvalidosError(
                'Este pedido de expedição não veio de um pedido de venda. '
                'Escolha a venda para poder trazer os itens dela.'
            )
        if pedido.status in (
            PedidoExpedicao.Status.EXPEDIDO,
            PedidoExpedicao.Status.ENTREGUE,
            PedidoExpedicao.Status.CANCELADO,
        ):
            # DEPOIS DE EXPEDIDO, A CARGA JA' SAIU: acrescentar linha aqui
            # seria reescrever o que o caminhao levou.
            raise DadosInvalidosError(
                f'Pedido {pedido.get_status_display().lower()} não recebe '
                'novos itens.'
            )

        pendentes = [l for l in cls.pendentes(pedido) if l['saldo'] > ZERO]
        if not pendentes:
            raise DadosInvalidosError(
                'Todos os itens desta venda já estão em pedidos de expedição.'
            )

        ordem = pedido.itens.count()
        criadas = []
        for linha in pendentes:
            ordem += 1
            item = linha['item']
            produto = linha['produto']
            criadas.append(ItemPedidoExpedicao.objects.create(
                pedido=pedido,
                item_venda=item,
                ordem=ordem,
                produto_codigo=getattr(produto, 'codigo', '') or '',
                produto_nome=str(getattr(produto, 'descricao', produto)),
                quantidade=linha['saldo'],
                unidade=cls._unidade(produto),
                # O PESO VEM DO CADASTRO DO PRODUTO, quando ele tem: e' o que
                # o MDF-e e a balanca vao cobrar depois. Sem cadastro fica
                # zero, e zero a' vista e' melhor do que um peso inventado.
                peso_kg=(getattr(produto, 'peso_bruto', None) or ZERO) * linha['saldo'],
                valor_unitario=item.valor_unitario or ZERO,
                observacao='',
            ))

        pedido.recalcular_totais()
        return {
            'criadas': criadas,
            'quantidade': sum((i.quantidade or ZERO for i in criadas), ZERO),
            'valor': sum((i.valor_total or ZERO for i in criadas), ZERO),
        }

    # ── Apoio ────────────────────────────────────────────────────────────

    @staticmethod
    def _unidade(produto) -> str:
        unidade = getattr(produto, 'unidade_medida', None)
        return (getattr(unidade, 'sigla', '') or 'UN')[:10]

    @staticmethod
    def _por_item(itens) -> dict:
        """Quanto cada linha da venda já rendeu nesta expedição."""
        totais = {}
        for item in itens:
            if item.item_venda_id:
                totais[item.item_venda_id] = (
                    totais.get(item.item_venda_id, ZERO) + (item.quantidade or ZERO)
                )
        return totais

    @staticmethod
    def _expedido_em_outras(venda, pedido) -> dict:
        """
        Quanto de cada linha já está em OUTRAS expedições vivas da venda.

        CANCELADA NÃO CONTA: o que estava nela voltou a ser saldo, e não
        descontá-la deixaria mercadoria vendida sem poder ser expedida.
        """
        agregado = (
            ItemPedidoExpedicao.objects
            .filter(item_venda__pedido=venda)
            .exclude(pedido_id=pedido.pk)
            .exclude(pedido__status__in=MORTOS)
            .values('item_venda_id')
            .annotate(total=Sum('quantidade'))
        )
        return {l['item_venda_id']: l['total'] or ZERO for l in agregado}
