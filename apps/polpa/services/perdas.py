"""
O que não virou produto — as duas metades da mesma pergunta.

SÃO DOIS REGISTROS DIFERENTES, e a diferença é dinheiro:

  · SUBPRODUTO saiu do processo e TEM DESTINO. Casca vira ração, semente vira
    óleo, bagaço vai para o produtor. Pode até entrar dinheiro — e quando vai
    para o caminhão da prefeitura, sai. Mora em `polpa.Subproduto`.
  · PERDA sumiu. Não tem destino, não volta, e o que ela deixa é custo. Mora em
    `producao.PerdaProducao`, pendurada na ordem do ERP.

Os dois pesam igual na balança e são o oposto um do outro no resultado. Juntá-los
numa tela sem nomear a diferença faria alguém somar bagaço vendido com polpa
derramada — que é exatamente a conta que não se quer.

POR QUE UMA TELA SÓ, então: porque a pergunta que se faz é "o que saiu da fruta
e não virou produto", e ela não se responde olhando metade. O rendimento que
falta está repartido entre as duas, e ver só uma esconde onde a fruta foi parar.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q

from apps.polpa.models import OrdemPolpa, Subproduto

ZERO = Decimal('0')


class PerdasService:
    """Leitura conjunta de subprodutos e perdas de uma filial."""

    @staticmethod
    def subprodutos(filial, filtros: dict | None = None):
        filtros = filtros or {}
        qs = (
            Subproduto.objects.for_filial(filial)
            .select_related('ordem', 'ordem__ordem', 'produto_estoque', 'etapa')
            .order_by('-data', '-id')
        )
        if filtros.get('tipo'):
            qs = qs.filter(tipo=filtros['tipo'])
        if filtros.get('destino'):
            qs = qs.filter(destino=filtros['destino'])
        if filtros.get('busca'):
            termo = filtros['busca']
            qs = qs.filter(
                Q(descricao__icontains=termo)
                | Q(destinatario__icontains=termo)
                | Q(ordem__ordem__numero__icontains=termo)
            )
        return qs

    @staticmethod
    def perdas(filial, filtros: dict | None = None):
        """
        As perdas das ordens DESTA filial.

        `PerdaProducao` não é `FilialScopedModel` -- ela pendura na ordem do
        ERP, e o recorte por filial passa pelas ordens de polpa. Filtrar por um
        campo `filial` que ela não tem devolveria erro; ignorar o recorte
        mostraria perda de outra unidade na tela desta.
        """
        from apps.producao.models import PerdaProducao

        ordens = OrdemPolpa.objects.for_filial(filial).values_list(
            'ordem_id', flat=True,
        )
        qs = (
            PerdaProducao.objects
            .filter(ordem_producao_id__in=list(ordens))
            # `etapa` aqui e' CharField, e nao FK -- em `Subproduto` e' FK, o
            # que torna facil pedir `select_related` nos dois por simetria e
            # levar um `FieldError` so' no primeiro acesso a' tela.
            .select_related('ordem_producao', 'produto')
            .order_by('-created_at')
        )
        if filtros and filtros.get('busca'):
            termo = filtros['busca']
            qs = qs.filter(
                Q(motivo_detalhado__icontains=termo)
                | Q(produto__descricao__icontains=termo)
                | Q(ordem_producao__numero__icontains=termo)
            )
        return qs

    @classmethod
    def resumo(cls, subprodutos, perdas) -> dict:
        """
        Os números do topo, somados sobre o que a tela JÁ CARREGOU.

        Somar de novo no banco daria outra consulta e, com filtro aplicado,
        outro resultado -- o topo dizendo um total e a tabela mostrando outro é
        o tipo de divergência que ninguém consegue explicar depois.
        """
        subprodutos = list(subprodutos)
        perdas = list(perdas)

        recebido = sum((s.valor_recebido or ZERO for s in subprodutos), ZERO)
        gasto = sum((s.custo_destinacao or ZERO for s in subprodutos), ZERO)
        return {
            'subprodutos': len(subprodutos),
            'kg_subproduto': sum((s.quantidade or ZERO for s in subprodutos), ZERO),
            'recebido': recebido,
            'gasto_destinacao': gasto,
            'resultado': recebido - gasto,
            # PENDENTE DE CRÉDITO é a lacuna que faz alguém comprar ração que
            # já está no pátio: o material foi separado para uso interno e o
            # almoxarifado não sabe.
            'pendentes': sum(1 for s in subprodutos if s.pendente_de_credito),
            'descartados': sum(
                1 for s in subprodutos
                if s.destino == Subproduto.Destino.DESCARTE
            ),
            'perdas': len(perdas),
            'kg_perdido': sum((p.quantidade or ZERO for p in perdas), ZERO),
            'custo_perdido': sum((p.impacto_custo or ZERO for p in perdas), ZERO),
            # PERDA EVITÁVEL é a única sobre a qual dá para agir. Misturá-la
            # com a inevitável faria a fábrica perseguir casca de manga.
            'evitaveis': sum(1 for p in perdas if p.perda_evitavel),
        }
