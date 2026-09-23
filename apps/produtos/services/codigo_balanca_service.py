"""Leitura de EAN-13 variável impresso por balanças Toledo Prix/MGV7."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from apps.produtos.services.codigo_barras_service import ean13_valido


class CodigoBalancaInvalido(ValueError):
    """O código não corresponde à composição configurada para a balança."""


@dataclass(frozen=True)
class DadosEANBalanca:
    codigo: str
    plu: str
    valor_bruto: int


@dataclass(frozen=True)
class LeituraEtiquetaBalanca:
    quantidade: Decimal
    valor_total: Decimal
    conteudo: str


def decodificar_ean_balanca(codigo, *, prefixo='20', plu_digitos=5):
    codigo = ''.join(caractere for caractere in str(codigo or '') if caractere.isdigit())
    prefixo = str(prefixo or '')
    try:
        plu_digitos = int(plu_digitos)
    except (TypeError, ValueError) as exc:
        raise CodigoBalancaInvalido('Quantidade de dígitos do PLU inválida.') from exc

    if len(codigo) != 13 or not ean13_valido(codigo):
        raise CodigoBalancaInvalido('O código não é um EAN-13 válido.')
    if len(prefixo) not in {1, 2} or not prefixo.isdigit() or not codigo.startswith(prefixo):
        raise CodigoBalancaInvalido('O prefixo não corresponde à configuração da balança.')
    if plu_digitos not in {3, 4, 5, 6}:
        raise CodigoBalancaInvalido('O PLU deve ter entre 3 e 6 dígitos.')

    inicio_valor = len(prefixo) + plu_digitos
    valor_texto = codigo[inicio_valor:-1]
    if not valor_texto:
        raise CodigoBalancaInvalido('O EAN não contém peso ou preço.')
    return DadosEANBalanca(
        codigo=codigo,
        plu=str(int(codigo[len(prefixo):inicio_valor] or '0')),
        valor_bruto=int(valor_texto),
    )


def calcular_leitura_etiqueta(produto, dados, *, conteudo='preco_total'):
    try:
        preco = Decimal(str(produto.preco_venda or 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CodigoBalancaInvalido('O produto está sem preço de venda válido.') from exc

    if dados.valor_bruto <= 0:
        raise CodigoBalancaInvalido('A etiqueta contém peso ou preço igual a zero.')
    if conteudo == 'peso':
        quantidade = Decimal(dados.valor_bruto) / Decimal('1000')
        valor_total = (quantidade * preco).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    elif conteudo == 'preco_total':
        if preco <= 0:
            raise CodigoBalancaInvalido('O produto está sem preço de venda válido.')
        valor_total = Decimal(dados.valor_bruto) / Decimal('100')
        quantidade = valor_total / preco
    else:
        raise CodigoBalancaInvalido('Conteúdo variável da etiqueta não reconhecido.')

    quantidade = quantidade.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    if quantidade <= 0:
        raise CodigoBalancaInvalido('Não foi possível calcular a quantidade da etiqueta.')
    return LeituraEtiquetaBalanca(
        quantidade=quantidade,
        valor_total=valor_total,
        conteudo=conteudo,
    )
