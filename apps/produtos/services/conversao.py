"""Arredondamento centralizado de quantidades comerciais.

Regra 5.10 (arredondamento): toda conversao entre apresentacao e unidade
base passa por `Decimal` com `ROUND_HALF_UP`, sempre atraves de
`quantizar()` — nunca reimplementar arredondamento em outro ponto do
codigo com uma regra diferente. `ApresentacaoService` (conversao entre
apresentacoes) usa esta funcao internamente.
"""
from decimal import Decimal, ROUND_HALF_UP

from apps.core.services.exceptions import DadosInvalidosError


def quantizar(valor, casas_decimais: int) -> Decimal:
    """Arredonda `valor` para `casas_decimais` casas usando ROUND_HALF_UP."""
    if casas_decimais < 0:
        raise DadosInvalidosError('casas_decimais nao pode ser negativo.')
    valor = Decimal(str(valor))
    quantizer = Decimal('1').scaleb(-casas_decimais)
    return valor.quantize(quantizer, rounding=ROUND_HALF_UP)


def quantizar_para_unidade(valor, unidade) -> Decimal:
    """Arredonda `valor` respeitando `unidade.casas_decimais`."""
    return quantizar(valor, unidade.casas_decimais)
