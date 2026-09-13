"""Geracao e verificacao de codigos de barras internos de produtos."""
import secrets

from django.db.models import Q

from apps.produtos.models import (
    Produto,
    ProdutoCodigoBarras,
    ProdutoFornecedorEquivalencia,
)


PREFIXO_EAN_INTERNO = '290'
MAX_TENTATIVAS_GERACAO = 100


def calcular_digito_verificador_ean13(base: str) -> str:
    """Calcula o digito verificador de uma base EAN-13 com 12 digitos."""
    if len(base) != 12 or not base.isdigit():
        raise ValueError('A base do EAN-13 deve conter 12 digitos.')
    soma = sum(
        int(digito) * (1 if indice % 2 == 0 else 3)
        for indice, digito in enumerate(base)
    )
    return str((10 - (soma % 10)) % 10)


def ean13_valido(codigo: str) -> bool:
    codigo = str(codigo or '').strip()
    return (
        len(codigo) == 13
        and codigo.isdigit()
        and codigo[-1] == calcular_digito_verificador_ean13(codigo[:12])
    )


def _produtos_da_empresa(empresa=None):
    queryset = Produto.objects.all()
    if empresa is not None:
        queryset = queryset.filter(
            Q(filial__empresa=empresa)
            | Q(filiais_vinculo__filial__empresa=empresa)
        ).distinct()
    return queryset


def codigo_barras_em_uso(codigo: str, *, empresa=None, produto_id=None) -> bool:
    """Confere todos os locais em que um produto pode guardar um codigo."""
    codigo = str(codigo or '').strip()
    if not codigo:
        return False

    produtos = _produtos_da_empresa(empresa)
    if produto_id:
        produtos = produtos.exclude(pk=produto_id)

    if produtos.filter(codigo_barras=codigo).exists():
        return True

    if ProdutoCodigoBarras.objects.filter(
        produto__in=produtos,
        ean=codigo,
    ).exists():
        return True

    if ProdutoFornecedorEquivalencia.objects.filter(
        produto__in=produtos,
        ean_utilizado=codigo,
    ).exists():
        return True

    # Funciona igualmente em PostgreSQL e SQLite. Alguns bancos nao suportam
    # o lookup JSON `contains`, entao a verificacao final e feita em Python.
    return any(
        codigo in {str(item).strip() for item in (extras or [])}
        for extras in produtos.values_list('codigos_barras_extras', flat=True)
        if isinstance(extras, list)
    )


def gerar_codigo_barras_unico(*, empresa=None) -> str:
    """Gera um EAN-13 interno valido e ainda nao usado pela empresa."""
    for _ in range(MAX_TENTATIVAS_GERACAO):
        corpo = f'{PREFIXO_EAN_INTERNO}{secrets.randbelow(1_000_000_000):09d}'
        codigo = f'{corpo}{calcular_digito_verificador_ean13(corpo)}'
        if not codigo_barras_em_uso(codigo, empresa=empresa):
            return codigo
    raise RuntimeError('Nao foi possivel gerar um codigo de barras unico. Tente novamente.')
