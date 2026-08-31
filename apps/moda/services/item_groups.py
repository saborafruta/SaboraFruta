"""Agrupamento visual dos itens da OP por produto.

A fábrica ainda precisa de uma linha interna por grade: grades diferentes podem
ter cortes, personalizações, preços e técnicas diferentes.  Na tela e nos PDFs,
porém, essas linhas são variantes do mesmo produto e devem aparecer juntas.
"""
from dataclasses import dataclass
from decimal import Decimal


GRADE_CORES = (
    ('#2563eb', '#dbeafe'),
    ('#7c3aed', '#ede9fe'),
    ('#059669', '#d1fae5'),
    ('#d97706', '#fef3c7'),
    ('#db2777', '#fce7f3'),
    ('#0891b2', '#cffafe'),
)


@dataclass
class GrupoItemOP:
    chave: tuple
    nome: str
    itens: list

    @property
    def quantidade(self) -> int:
        return sum(item.quantidade for item in self.itens)

    @property
    def subtotal(self) -> Decimal:
        return sum((item.subtotal for item in self.itens), Decimal('0'))

    @property
    def multiplas_grades(self) -> bool:
        return len(self.itens) > 1

    @property
    def valor_unitario_unico(self):
        valores = {item.valor_unitario for item in self.itens}
        return valores.pop() if len(valores) == 1 else None


def _chave(item):
    if item.produto_id:
        return ('produto', item.produto_id)
    return (
        'livre', (item.descricao or '').strip().casefold(),
        item.modelo_id, (item.referencia or '').strip().casefold(),
    )


def _nome_base(item):
    return item.produto.nome if item.produto_id else (
        item.descricao or 'Item sem descrição'
    )


def agrupar_itens_op(itens) -> list[GrupoItemOP]:
    """Agrupa variantes preservando a ordem em que o produto entrou na OP."""
    grupos = []
    por_chave = {}
    for item in itens:
        chave = _chave(item)
        grupo = por_chave.get(chave)
        if grupo is None:
            grupo = GrupoItemOP(chave=chave, nome=_nome_base(item), itens=[])
            por_chave[chave] = grupo
            grupos.append(grupo)
        indice = len(grupo.itens)
        cor, fundo = GRADE_CORES[indice % len(GRADE_CORES)]
        item.grade_cor = cor
        item.grade_fundo = fundo
        item.grade_rotulo = (
            item.grade_tamanho.nome if item.grade_tamanho_id else 'Sem grade'
        )
        item.nome_base_op = grupo.nome
        grupo.itens.append(item)
    return grupos
