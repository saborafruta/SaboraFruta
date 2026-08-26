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


class BuscaClientes:
    """
    A busca que alimenta o campo de cliente do pedido.

    SEPARADA DA CARTEIRA de propósito: `listar` agrega pedidos, peças e
    valores em cada linha — o que a carteira precisa mostrar e o campo do
    formulário não. Rodar aquela consulta a cada tecla digitada seria pagar
    cinco junções para exibir um nome.

    Devolve TAMBÉM o telefone e o contato: quem escolhe o cliente no pedido
    quer o telefone preenchido junto, e uma segunda ida ao servidor só para
    isso deixaria o campo piscando depois de escolhido.
    """

    # O suficiente para achar sem rolar. Mais do que isso não ajuda: se o
    # nome não está nos vinte primeiros, o termo é que está curto demais.
    LIMITE = 20

    @staticmethod
    def procurar(filial, termo: str = '', limite: int | None = None) -> list[Cliente]:
        consulta = Cliente.objects.for_filial(filial).filter(ativo=True)

        termo = (termo or '').strip()
        if termo:
            # Cada palavra pode aparecer em qualquer campo e em qualquer
            # ordem. Assim "diego macedo", "macedo diego" e partes do
            # telefone/documento encontram o mesmo cadastro.
            for parte in termo.split():
                digitos = ''.join(c for c in parte if c.isdigit())
                filtro = (
                    Q(razao_social__icontains=parte)
                    | Q(nome_fantasia__icontains=parte)
                    | Q(contato_nome__icontains=parte)
                    | Q(cidade__icontains=parte)
                )
                if digitos:
                    filtro |= (
                        Q(cpf_cnpj__contains=digitos)
                        | Q(celular__icontains=digitos)
                        | Q(telefone__icontains=digitos)
                    )
                consulta = consulta.filter(filtro)

        return list(consulta.order_by('razao_social')[:(limite or BuscaClientes.LIMITE)])

    @staticmethod
    def como_dicionario(cliente: Cliente) -> dict:
        """O cliente do jeito que o campo do pedido consome."""
        return {
            'id': cliente.pk,
            'nome': cliente.nome_display,
            'razao_social': cliente.razao_social,
            'documento': _documento(cliente.cpf_cnpj),
            # Celular primeiro: é o número de WhatsApp, que é por onde o
            # pedido é enviado ao cliente.
            'telefone': cliente.celular or cliente.telefone or '',
            'contato': cliente.contato_nome or '',
            'cidade': cliente.cidade or '',
        }


def _documento(valor: str) -> str:
    """CPF/CNPJ com pontuação — guardado sem ela, lido com ela."""
    digitos = ''.join(c for c in (valor or '') if c.isdigit())
    if len(digitos) == 11:
        return f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}'
    if len(digitos) == 14:
        return (f'{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/'
                f'{digitos[8:12]}-{digitos[12:]}')
    return digitos
