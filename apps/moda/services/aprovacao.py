"""
A fila de aprovação do comercial — o passo 7 e o passo 10 vistos de cima.

A TELA DE UM PEDIDO SÓ JÁ EXISTE (`/pedidos/<id>/aprovacao/`). O que faltava
é a pergunta que se faz de manhã: **o que está parado esperando alguém?**
Sem ela, saber quais pedidos aguardam liberação exigia abrir um por um.

SÃO TRÊS ESPERAS DIFERENTES, e misturá-las numa lista só esconde a única que
é urgente:

  · ESPERANDO A CASA — o pedido está pronto e ninguém liberou. A demora é
    nossa, e é a que o cliente não vê;
  · ESPERANDO O CLIENTE — liberado, link enviado, sem resposta. A demora é
    dele, mas cobrar é nosso;
  · PEDIU AJUSTE — o cliente respondeu NÃO, com motivo. É o que mais parece
    parado e o que mais precisa de gente.

ORÇAMENTO NÃO ENTRA na fila de liberação: proposta que ainda não virou
pedido não tem o que liberar. Ela aparece na tela de Orçamentos, que é onde
se decide fechar. A exceção é o orçamento JÁ liberado -- alguém decidiu
mostrá-lo ao cliente, e a partir daí ele está esperando resposta como
qualquer outro.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils import timezone

from apps.moda.models import AprovacaoPedido, PedidoProducao

ZERO = Decimal('0')

# Depois disto, esperar virou esquecer. Não é regra de negócio inventada: é
# o ponto em que o comercial de confecção liga para saber se o cliente ainda
# quer -- e o prazo de produção já começou a apertar.
DIAS_PARA_COBRAR = 3

# Status que não entram na fila: a proposta ainda não é compromisso, e o
# cancelado saiu do fluxo.
FORA_DA_FILA = (
    PedidoProducao.Status.ORCAMENTO,
    PedidoProducao.Status.CANCELADO,
)


@dataclass
class Linha:
    """Um pedido na fila, com o tempo que ele está parado."""

    pedido: PedidoProducao
    aprovacao: AprovacaoPedido | None
    dias_parado: int
    faltas: list[str]

    @property
    def cobrar(self) -> bool:
        return self.dias_parado >= DIAS_PARA_COBRAR

    @property
    def pode_liberar(self) -> bool:
        return not self.faltas


class FilaAprovacaoService:

    @staticmethod
    def base(filial):
        return (
            PedidoProducao.objects.for_filial(filial)
            .select_related('cliente', 'vendedor', 'aprovacao')
            .prefetch_related('itens')
        )

    @classmethod
    def montar(cls, filial, busca: str = '', hoje=None) -> dict:
        hoje = hoje or timezone.localdate()
        agora = timezone.now()

        consulta = cls.base(filial)
        if busca:
            from django.db.models import Q

            termo = busca.strip()
            consulta = consulta.filter(
                Q(numero__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
            )

        para_liberar, com_cliente, ajuste, aprovados = [], [], [], []

        for pedido in consulta:
            aprovacao = getattr(pedido, 'aprovacao', None)
            liberado = bool(aprovacao and aprovacao.liberado)

            if pedido.status in FORA_DA_FILA and not liberado:
                continue

            if not liberado:
                para_liberar.append(Linha(
                    pedido=pedido, aprovacao=aprovacao,
                    # Parado desde a data do pedido: é quando o relógio do
                    # cliente começou a correr, não quando alguém abriu a tela.
                    dias_parado=(hoje - pedido.data_pedido).days,
                    faltas=cls.faltas(pedido),
                ))
                continue

            dias = (agora - aprovacao.liberado_em).days
            linha = Linha(
                pedido=pedido, aprovacao=aprovacao,
                dias_parado=dias, faltas=[],
            )

            if aprovacao.pediu_ajuste:
                ajuste.append(linha)
            elif aprovacao.aprovado_pelo_cliente:
                aprovados.append(linha)
            else:
                com_cliente.append(linha)

        # O mais parado primeiro em toda fila: a lista é de coisa a resolver,
        # e ordenar do mais novo esconderia justamente o esquecido.
        for fila in (para_liberar, com_cliente, ajuste):
            fila.sort(key=lambda l: -l.dias_parado)
        # Os aprovados são histórico recente: do último para trás.
        aprovados.sort(key=lambda l: l.aprovacao.respondido_em or agora, reverse=True)

        return {
            'para_liberar': para_liberar,
            'com_cliente': com_cliente,
            'ajuste': ajuste,
            'aprovados': aprovados[:10],
            'resumo': cls.resumo(para_liberar, com_cliente, ajuste),
        }

    @staticmethod
    def faltas(pedido) -> list[str]:
        """
        O que impede ESTE pedido de ser liberado ao cliente.

        Bem menos que as onze validações da produção, e de propósito: liberar
        é dizer "o preço e o prazo estão certos, pode mostrar". Cobrar ficha
        técnica e roteiro aqui travaria a conversa com o cliente por causa de
        um cadastro que o PCP ainda vai fazer.

        Mas o que o CLIENTE vai olhar tem de estar lá — mandar um link com o
        pedido vazio é queimar a única chance de ele responder rápido.
        """
        faltas = []
        itens = list(pedido.itens.all())

        if not itens:
            faltas.append('sem produto lançado')
        if not pedido.data_prevista_entrega:
            faltas.append('sem data de entrega')
        if itens and not any(i.valor_unitario for i in itens):
            faltas.append('sem valor nos produtos')
        return faltas

    @staticmethod
    def resumo(para_liberar, com_cliente, ajuste) -> dict:
        esperando = para_liberar + com_cliente
        return {
            'para_liberar': len(para_liberar),
            'prontos_para_liberar': sum(1 for l in para_liberar if l.pode_liberar),
            'com_cliente': len(com_cliente),
            'ajuste': len(ajuste),
            'cobrar': sum(1 for l in com_cliente if l.cobrar),
            # O mais antigo de tudo que espera: é o número que diz se a fila
            # está sob controle ou se alguém foi esquecido.
            'mais_parado': max((l.dias_parado for l in esperando), default=0),
            'pecas': sum(l.pedido.quantidade_total for l in esperando),
            'valor': sum((l.pedido.valor_total for l in esperando), ZERO),
        }
