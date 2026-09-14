"""Conversao entre apresentacoes de um produto e sua unidade base.

Ver docstring de `apps.produtos.models.apresentacao.ProdutoApresentacao`
para a decisao de modelagem: o fator gravado no banco e sempre absoluto e
direto para a unidade base do produto. A entrada encadeada (ex: "1 CX = 12
PCT", "1 PCT = 10 UN") e so uma conveniencia de digitacao resolvida aqui —
`calcular_fator_encadeado` multiplica os elos e devolve o fator absoluto
que o model/admin deve gravar. O service nunca persiste uma cadeia.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import Produto, ProdutoApresentacao
from apps.produtos.services.conversao import quantizar_para_unidade


class ApresentacaoService:
    """Conversoes e consultas sobre apresentacoes de venda/compra de um produto."""

    @staticmethod
    def _to_decimal(valor, *, campo: str) -> Decimal:
        try:
            decimal_valor = Decimal(str(valor))
        except (InvalidOperation, TypeError):
            raise DadosInvalidosError(f'{campo} invalido: {valor!r}')
        if decimal_valor <= 0:
            raise DadosInvalidosError(f'{campo} deve ser maior que zero.')
        return decimal_valor

    @classmethod
    def calcular_fator_encadeado(cls, *elos) -> Decimal:
        """
        Multiplica uma cadeia de fatores digitados pelo usuario e retorna o
        fator absoluto resultante.

        Ex: "1 CX = 12 PCT" e "1 PCT = 10 UN" chegam aqui como (12, 10) e o
        resultado e 120 — o fator que a apresentacao CX deve gravar,
        absoluto e direto para a unidade base (UN).
        """
        if not elos:
            raise DadosInvalidosError('Informe ao menos um fator na cadeia de conversao.')
        fator = Decimal('1')
        for indice, elo in enumerate(elos, start=1):
            fator *= cls._to_decimal(elo, campo=f'Fator do elo {indice}')
        return fator

    @staticmethod
    def converter(apresentacao: ProdutoApresentacao, quantidade, *, para: str = 'base') -> Decimal:
        """
        Converte `quantidade` na apresentacao informada.

        `para='base'` (padrao): quantidade esta na apresentacao, retorna na
        unidade base do produto.
        `para='apresentacao'`: quantidade esta na unidade base do produto,
        retorna na apresentacao.
        """
        quantidade = ApresentacaoService._to_decimal(quantidade, campo='Quantidade')
        if para == 'base':
            resultado = apresentacao.converter_para_base(quantidade)
            return quantizar_para_unidade(resultado, apresentacao.produto.unidade_medida)
        if para == 'apresentacao':
            resultado = apresentacao.converter_de_base(quantidade)
            return quantizar_para_unidade(resultado, apresentacao.unidade)
        raise DadosInvalidosError(f"Parametro 'para' invalido: {para!r}. Use 'base' ou 'apresentacao'.")

    @staticmethod
    def converter_entre_apresentacoes(
        origem: ProdutoApresentacao, destino: ProdutoApresentacao, quantidade,
    ) -> Decimal:
        """Converte uma quantidade da apresentacao de origem para a de destino."""
        if origem.produto_id != destino.produto_id:
            raise DadosInvalidosError(
                'Nao e possivel converter entre apresentacoes de produtos diferentes.',
            )
        quantidade_base = ApresentacaoService.converter(origem, quantidade, para='base')
        resultado = destino.converter_de_base(quantidade_base)
        return quantizar_para_unidade(resultado, destino.unidade)

    @staticmethod
    def apresentacao_principal_venda(produto: Produto) -> ProdutoApresentacao | None:
        """Apresentacao marcada como principal de venda para o produto, entre as ativas."""
        return (
            ProdutoApresentacao.objects.ativas()
            .filter(produto=produto, principal_venda=True)
            .first()
        )

    @staticmethod
    def apresentacao_principal_compra(produto: Produto) -> ProdutoApresentacao | None:
        """Apresentacao marcada como principal de compra para o produto, entre as ativas."""
        return (
            ProdutoApresentacao.objects.ativas()
            .filter(produto=produto, principal_compra=True)
            .first()
        )

    @staticmethod
    def listar_ativas(produto: Produto):
        return ProdutoApresentacao.objects.ativas().filter(produto=produto).order_by('fator_conversao')
