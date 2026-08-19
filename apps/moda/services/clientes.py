"""
A carteira de clientes vista pela confecção.

NÃO EXISTE "CLIENTE DA MODA". É o mesmo `cadastros.Cliente` do ERP inteiro —
o mesmo que o financeiro cobra, que o fiscal usa na nota e que o PDV atende.
Uma tabela própria aqui seria a segunda base de clientes que este trabalho
todo veio eliminar: o endereço mudaria num lugar e não no outro, e a nota
sairia com o antigo.

O QUE A TELA ACRESCENTA é o olhar do vertical: quantos pedidos de confecção
aquele cliente tem, quantas peças, quanto valem, quando foi o último e
quantos estão atrasados. Isso o cadastro geral não sabe — e é justamente o
que o comercial da confecção precisa ver antes de ligar para alguém.

OS NÚMEROS SAEM NUMA CONSULTA SÓ, com agregação no banco. Percorrer os
pedidos de cada cliente em Python custaria uma consulta por linha da lista,
e a carteira de uma confecção tem centenas de nomes.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.moda.models import PedidoProducao

ZERO = Decimal('0')

# Orçamento não é venda e cancelado não aconteceu: os dois fora de tudo que
# é volume ou dinheiro, igual ao dashboard. Carteira que soma proposta faz
# o comercial achar que vendeu o que não vendeu.
PEDIDOS_QUE_CONTAM = ~Q(pedidos_moda__status__in=[
    PedidoProducao.Status.ORCAMENTO,
    PedidoProducao.Status.CANCELADO,
])


class CarteiraService:

    @staticmethod
    def listar(filial, busca: str = '', so_com_pedido: bool = False):
        """
        Os clientes da filial com os números do vertical.

        `Count(distinct=True)` porque a junção com os itens multiplica as
        linhas: sem isso, um pedido com três produtos contaria como três
        pedidos.
        """
        hoje = timezone.localdate()
        em_producao = PEDIDOS_QUE_CONTAM & ~Q(
            pedidos_moda__status=PedidoProducao.Status.ENTREGUE,
        )

        valor = Coalesce(
            Sum(
                F('pedidos_moda__itens__quantidade')
                * F('pedidos_moda__itens__valor_unitario'),
                filter=PEDIDOS_QUE_CONTAM,
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            Value(ZERO, output_field=DecimalField(max_digits=14, decimal_places=2)),
        )

        consulta = (
            Cliente.objects.for_filial(filial)
            .annotate(
                pedidos_moda_total=Count(
                    'pedidos_moda', filter=PEDIDOS_QUE_CONTAM, distinct=True,
                ),
                pedidos_moda_abertos=Count(
                    'pedidos_moda', filter=em_producao, distinct=True,
                ),
                pedidos_moda_atrasados=Count(
                    'pedidos_moda',
                    filter=em_producao & Q(
                        pedidos_moda__data_prevista_entrega__lt=hoje,
                    ),
                    distinct=True,
                ),
                pecas_moda=Coalesce(
                    Sum('pedidos_moda__itens__quantidade', filter=PEDIDOS_QUE_CONTAM),
                    Value(0),
                ),
                valor_moda=valor,
                ultimo_pedido_moda=Max(
                    'pedidos_moda__data_pedido', filter=PEDIDOS_QUE_CONTAM,
                ),
            )
        )

        if busca:
            termo = busca.strip()
            consulta = consulta.filter(
                Q(razao_social__icontains=termo)
                | Q(nome_fantasia__icontains=termo)
                | Q(cpf_cnpj__icontains=termo)
                | Q(cidade__icontains=termo)
            )
        if so_com_pedido:
            consulta = consulta.filter(pedidos_moda_total__gt=0)

        # Quem tem pedido primeiro, e dentro disso o mais recente: a carteira
        # é lida de cima, e quem nunca comprou não pode ocupar as primeiras
        # linhas por ordem alfabética.
        return consulta.order_by(
            '-pedidos_moda_total', '-ultimo_pedido_moda', 'razao_social',
        )

    @staticmethod
    def resumo(clientes) -> dict:
        """Os totais do topo, sobre a lista JÁ filtrada."""
        com_pedido = [c for c in clientes if c.pedidos_moda_total]
        return {
            'clientes': len(clientes),
            'com_pedido': len(com_pedido),
            'pecas': sum(c.pecas_moda or 0 for c in clientes),
            'valor': sum((c.valor_moda or ZERO for c in clientes), ZERO),
            'com_atraso': sum(1 for c in clientes if c.pedidos_moda_atrasados),
        }
