"""
Orçamentos — a proposta antes de virar pedido.

NÃO EXISTE MODELO DE ORÇAMENTO. Orçamento é o `PedidoProducao` no primeiro
status, e fechá-lo é uma troca de status — não uma cópia de um registro para
outro. Duas tabelas fariam o número do pedido mudar no meio do caminho, e a
arte, a grade e as pessoas já lançadas teriam de ser transferidas: é onde
esse tipo de sistema costuma perder informação.

Por isso o orçamento já nasce com tudo que o pedido tem. Fechar não move
nada de lugar; só diz que a proposta virou compromisso.

O QUE ESTA TELA ACRESCENTA à lista de pedidos é a pergunta do comercial:
quais propostas ainda estão de pé, quais estão prontas para fechar, e quais
estão esfriando. Um orçamento parado há três semanas não é igual a um de
ontem, e a lista comum não faz essa distinção.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from apps.moda.models import PedidoProducao

ZERO = Decimal('0')

# Depois de duas semanas sem virar pedido, a proposta esfriou: ou o cliente
# sumiu, ou alguém precisa ligar. Não é regra de negócio inventada — é o
# ponto em que o comercial de confecção costuma refazer o preço, porque
# tecido e prazo já mudaram.
DIAS_PARA_ESFRIAR = 14


@dataclass
class Linha:
    """Um orçamento com o que falta nele para poder fechar."""
    pedido: PedidoProducao
    dias_parado: int
    faltas: list[str]

    @property
    def pode_fechar(self) -> bool:
        return not self.faltas

    @property
    def esfriando(self) -> bool:
        return self.dias_parado >= DIAS_PARA_ESFRIAR


class OrcamentoService:

    @staticmethod
    def base(filial):
        return (
            PedidoProducao.objects.for_filial(filial)
            .filter(status=PedidoProducao.Status.ORCAMENTO)
            .select_related('cliente', 'vendedor')
            .prefetch_related('itens')
            .annotate(total_itens=Count('itens', distinct=True))
        )

    @classmethod
    def listar(cls, filial, busca: str = '', hoje=None) -> list[Linha]:
        hoje = hoje or timezone.localdate()
        consulta = cls.base(filial)

        if busca:
            termo = busca.strip()
            consulta = consulta.filter(
                Q(numero__icontains=termo)
                | Q(cliente__razao_social__icontains=termo)
                | Q(cliente__nome_fantasia__icontains=termo)
                | Q(contato_nome__icontains=termo)
            )

        # Mais antigo primeiro: a lista é de coisa a resolver, e o que está
        # parado há mais tempo é o que precisa de decisão. Ordenar do mais
        # novo esconderia justamente o esquecido lá embaixo.
        return [
            Linha(
                pedido=pedido,
                dias_parado=(hoje - pedido.data_pedido).days,
                faltas=cls.faltas(pedido),
            )
            for pedido in consulta.order_by('data_pedido', 'numero')
        ]

    @staticmethod
    def faltas(pedido) -> list[str]:
        """
        O que impede ESTE orçamento de virar pedido.

        Bem menos que as onze validações da produção, e de propósito: fechar
        um orçamento é dizer "o cliente aceitou", não mandar cortar tecido.
        Cobrar ficha técnica e roteiro aqui travaria a venda por causa de um
        cadastro que o PCP ainda vai fazer.
        """
        faltas = []
        itens = list(pedido.itens.all())

        if not itens:
            faltas.append('nenhum produto lançado')
        if not pedido.data_prevista_entrega:
            faltas.append('sem data de entrega')
        if itens and not any(i.valor_unitario for i in itens):
            faltas.append('sem valor nos produtos')
        return faltas

    @staticmethod
    def resumo(linhas: list[Linha]) -> dict:
        prontos = [l for l in linhas if l.pode_fechar]
        return {
            'total': len(linhas),
            'prontos': len(prontos),
            'incompletos': len(linhas) - len(prontos),
            'esfriando': sum(1 for l in linhas if l.esfriando),
            'pecas': sum(l.pedido.quantidade_total for l in linhas),
            'valor': sum((l.pedido.valor_total for l in linhas), ZERO),
        }

    @classmethod
    def fechar(cls, pedido, usuario=None) -> None:
        """
        A proposta vira pedido: uma troca de status, e nada mais.

        A validação é a daqui, não a da produção. Quem fecha está
        registrando um "sim" do cliente — as onze validações continuam
        cobradas depois, na hora de emitir a ordem.
        """
        from apps.core.services.exceptions import DomainError

        if pedido.status != PedidoProducao.Status.ORCAMENTO:
            raise DomainError(
                f'Este pedido já não é orçamento (está em '
                f'"{pedido.get_status_display()}").'
            )

        faltas = cls.faltas(pedido)
        if faltas:
            raise DomainError(
                'Para fechar o orçamento falta: ' + ', '.join(faltas) + '.'
            )

        pedido.status = PedidoProducao.Status.CONFIRMADO
        pedido.save(update_fields=['status', 'updated_at'])
