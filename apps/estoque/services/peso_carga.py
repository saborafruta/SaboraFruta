"""
Peso bruto da carga para o MDF-e.

O MDF-e exige o peso bruto total. Antes, o cálculo usava só
`Produto.peso_bruto`; qualquer produto sem esse campo zerava o total e
bloqueava a emissão — mesmo quando o peso era dedutível do próprio cadastro
(um item vendido em KG tem a quantidade como peso).

Este módulo resolve o peso por uma hierarquia de fontes, sempre a partir de
dado JÁ CADASTRADO. Peso em MDF-e é declaração fiscal, então nada aqui inventa
número: se nenhuma fonte responder, o produto continua sendo reportado como
pendente, e não estimado.

Ordem das fontes:

1. `peso_bruto` — o que o fiscal espera; inclui embalagem.
2. `peso_liquido` — quando só ele existe. Subestima pela embalagem, o que é
   aceitável e muito melhor que zerar a carga inteira.
3. Unidade de medida do tipo "peso" — se o item é vendido em KG/G/TON, a
   quantidade **é** o peso; converte pelo `fator_conversao_base` da unidade.

Tudo é normalizado para quilogramas, que é a unidade do campo do MDF-e.
"""
from __future__ import annotations

from decimal import Decimal

#: multiplicadores para kg, conforme `Produto.unidade_peso`
_PARA_KG = {
    'kg': Decimal('1'),
    'g': Decimal('0.001'),
    'ton': Decimal('1000'),
}


def peso_unitario_kg(produto) -> tuple[Decimal, str]:
    """
    Peso de UMA unidade do produto, em kg, e a origem usada.

    Devolve `(Decimal('0'), 'ausente')` quando nenhuma fonte responde — o
    chamador decide o que fazer, em vez de receber um número inventado.
    """
    fator = _PARA_KG.get((produto.unidade_peso or 'kg').lower(), Decimal('1'))

    if produto.peso_bruto and produto.peso_bruto > 0:
        return produto.peso_bruto * fator, 'peso_bruto'

    if produto.peso_liquido and produto.peso_liquido > 0:
        return produto.peso_liquido * fator, 'peso_liquido'

    # Produto vendido a peso: a quantidade ja e o peso. O fator_conversao_base
    # leva a unidade (g, ton...) para a base em kg.
    unidade = produto.unidade_medida
    if unidade is not None and unidade.tipo == 'peso':
        base = unidade.fator_conversao_base or Decimal('1')
        return Decimal(str(base)), 'unidade_peso'

    return Decimal('0'), 'ausente'


def calcular_peso_bruto(itens) -> dict:
    """
    Peso bruto total da carga.

    `itens` é uma lista de `(produto, quantidade)`.

    Devolve:
        {
          'peso_kg': Decimal,          # total, arredondado a 3 casas
          'pendentes': [descricao],    # produtos sem nenhuma fonte de peso
          'origens': {descricao: origem},
        }

    `pendentes` vazio significa que a carga inteira tem peso conhecido.
    """
    total = Decimal('0')
    pendentes: list[str] = []
    origens: dict[str, str] = {}

    for produto, quantidade in itens:
        unitario, origem = peso_unitario_kg(produto)
        origens[produto.descricao] = origem
        if unitario <= 0:
            pendentes.append(produto.descricao)
            continue
        total += unitario * Decimal(str(quantidade or 0))

    return {
        'peso_kg': total.quantize(Decimal('0.001')),
        'pendentes': sorted(set(pendentes)),
        'origens': origens,
    }
