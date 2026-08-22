"""
Leitura e gravação da estrutura do produto.

A árvore sai ACHATADA, com o nível em cada linha, e não aninhada: template
Django não faz recursão sem `include` de si mesmo, e uma lista com `nivel`
rende a mesma leitura com indentação simples — sem um segundo arquivo de
template só para chamar a si próprio.

CICLO é o risco central. Nada no banco impede gravar A dentro de B e B
dentro de A, e a partir daí qualquer leitura roda para sempre. A defesa
está em dois lugares de propósito:

  - `pode_incluir` barra na gravação, com uma frase que diz onde está o
    problema;
  - `arvore` corta na leitura, marcando o nó, porque linha criada por
    importação ou por shell nunca passou pela view.
"""
from __future__ import annotations

from decimal import Decimal

from ..models import EstruturaProduto, ProdutoModa

# Um teto de segurança para a leitura, além do corte por ciclo: estrutura
# de confecção não passa de dois ou três níveis, e uma cadeia mais funda
# que isso é erro de cadastro, não caso de uso.
NIVEL_MAXIMO = 10


def custo_proprio(produto) -> Decimal:
    """
    O custo dos MATERIAIS da peça, o que a ficha técnica dela soma.

    Sem ficha o custo é zero, e não um erro: um conjunto pode não ter ficha
    nenhuma, porque tudo que ele custa vem das partes.
    """
    ficha = getattr(produto, 'ficha', None)
    if ficha is None:
        return Decimal('0')
    return ficha.custo_estimado


def _filhos(produto):
    return (
        EstruturaProduto.objects.filter(pai=produto)
        .select_related('componente', 'componente__ficha')
        .order_by('ordem', 'id')
    )


def arvore(produto, quantidade=Decimal('1')) -> list[dict]:
    """
    A estrutura explodida, uma linha por componente, com o nível.

    `quantidade_total` já vem multiplicada pela cadeia inteira: dois
    conjuntos que levam duas camisas cada dão quatro camisas na linha da
    camisa. É o número que a produção usa, e recalculá-lo na tela seria
    repetir a conta em outro lugar.
    """
    linhas: list[dict] = []

    def descer(pai, quantidade_acumulada, nivel, caminho):
        if nivel > NIVEL_MAXIMO:
            return
        for elo in _filhos(pai):
            filho = elo.componente
            total = quantidade_acumulada * elo.quantidade
            repetido = filho.pk in caminho
            linhas.append({
                'elo': elo,
                'produto': filho,
                'nivel': nivel,
                'quantidade': elo.quantidade,
                'quantidade_total': total,
                'custo_proprio': custo_proprio(filho),
                'custo_total': custo_proprio(filho) * total,
                # Nó que reaparece no próprio caminho: mostrado uma vez,
                # marcado, e não descido -- senão a leitura não termina.
                'ciclo': repetido,
                'tem_filhos': not repetido and EstruturaProduto.objects.filter(pai=filho).exists(),
            })
            if not repetido:
                descer(filho, total, nivel + 1, caminho | {filho.pk})

    descer(produto, Decimal(quantidade), 1, {produto.pk})
    return linhas


def custo_estrutura(produto, _caminho=None) -> Decimal:
    """
    Custo da peça mais o das partes, descendo a árvore.

    O total é somado na leitura, e não gravado — mesmo motivo da ficha
    técnica: um custo gravado fica velho no instante em que alguém corrige
    o preço de um aviamento três níveis abaixo.
    """
    caminho = _caminho or {produto.pk}
    total = custo_proprio(produto)
    for elo in _filhos(produto):
        if elo.componente_id in caminho:
            continue
        total += custo_estrutura(elo.componente, caminho | {elo.componente_id}) * elo.quantidade
    return total.quantize(Decimal('0.01'))


def descendentes(produto, _vistos=None) -> set:
    """Todo produto alcançável descendo a partir deste."""
    vistos = _vistos if _vistos is not None else set()
    for elo in EstruturaProduto.objects.filter(pai=produto).only('componente_id'):
        if elo.componente_id in vistos:
            continue
        vistos.add(elo.componente_id)
        descendentes_de = ProdutoModa.objects.filter(pk=elo.componente_id).first()
        if descendentes_de is not None:
            descendentes(descendentes_de, vistos)
    return vistos


def pode_incluir(pai, componente) -> tuple[bool, str]:
    """
    Se dá para pôr `componente` dentro de `pai`, e o porquê quando não dá.

    A frase do erro nomeia o problema em vez de dizer "operação inválida":
    quem está montando um conjunto precisa saber QUAL peça fecha o ciclo
    para desfazer a que está errada.
    """
    if pai.pk == componente.pk:
        return False, 'Um produto não pode ser componente de si mesmo.'
    if pai.filial_id != componente.filial_id:
        return False, 'O componente precisa ser da mesma filial.'
    if EstruturaProduto.objects.filter(pai=pai, componente=componente).exists():
        return False, (
            f'“{componente.nome}” já está na estrutura. Para levar mais de '
            'uma unidade, aumente a quantidade da linha existente.'
        )
    if pai.pk in descendentes(componente):
        return False, (
            f'Isso fecharia um ciclo: “{pai.nome}” já aparece dentro de '
            f'“{componente.nome}”, direta ou indiretamente.'
        )
    return True, ''
