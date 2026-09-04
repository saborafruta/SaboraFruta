"""Agrupamento visual dos itens da OP por produto.

A fábrica ainda precisa de uma linha interna por grade: grades diferentes podem
ter cortes e personalizações diferentes. Na tela e no PDF, essas linhas ficam
juntas somente quando pertencem ao mesmo produto e compartilham a mesma ficha
técnica. Uma especificação diferente precisa continuar visível em bloco próprio.
"""
import json
from dataclasses import dataclass
from decimal import Decimal
import unicodedata


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


def _texto(valor):
    return ' '.join(str(valor or '').split()).casefold()


def _rotulo_normalizado(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    return ' '.join(
        ''.join(letra for letra in texto if not unicodedata.combining(letra))
        .casefold().split()
    )


def _observacoes_sem_tipo_impressao(observacoes):
    """A impressão é comparada pela relação real, não pelo resumo replicado."""
    linhas = []
    for linha in str(observacoes or '').splitlines():
        rotulo = linha.split(':', 1)[0] if ':' in linha else ''
        if _rotulo_normalizado(rotulo) in {
            'tipo impressao', 'tipo de impressao',
        }:
            continue
        linhas.append(linha)
    return _texto('\n'.join(linhas))


def _personalizacoes_ativas(item):
    return [
        personalizacao for personalizacao in item.personalizacoes.all()
        if _texto(personalizacao.tecnica) not in {
            '', 'n/a', 'sem impressao', 'sem_impressao',
        }
    ]


def tipos_impressao_item(item):
    """Rótulos das impressões efetivamente salvas; sem relação significa N/A."""
    tecnicas = []
    for personalizacao in _personalizacoes_ativas(item):
        rotulo = personalizacao.get_tecnica_display()
        if rotulo not in tecnicas:
            tecnicas.append(rotulo)
    return tecnicas or ['N/A']


def _assinatura_personalizacoes(item):
    """Campos técnicos da aplicação, sem arquivo/ID, em ordem canônica."""
    return tuple(sorted(
        (
            _texto(personalizacao.tipo),
            _texto(personalizacao.tecnica),
            _texto(personalizacao.local),
            _texto(personalizacao.nome_personalizado),
            _texto(personalizacao.numero_personalizado),
            _texto(personalizacao.patrocinios),
            personalizacao.quantidade_patrocinadores,
            _texto(personalizacao.observacoes),
        )
        for personalizacao in _personalizacoes_ativas(item)
    ))


def _configuracao_tecnica_conjunto(configuracao):
    """Retira apenas as grades do conjunto antes de comparar sua ficha."""
    resultado = {}
    for componente, dados in sorted((configuracao or {}).items()):
        if not isinstance(dados, dict):
            resultado[componente] = dados
            continue
        resultado[componente] = {
            chave: valor for chave, valor in dados.items()
            if chave not in {'grades', 'gradePorGrade'}
        }
    return json.dumps(
        resultado, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str,
    )


def _ficha_tecnica(item):
    return (
        item.modelo_id,
        item.cor_id,
        item.tecido_id,
        _texto(item.gola),
        _texto(item.manga),
        _texto(item.acabamento),
        _observacoes_sem_tipo_impressao(item.observacoes),
        _assinatura_personalizacoes(item),
        _configuracao_tecnica_conjunto(item.configuracao_conjunto),
    )


def _chave(item):
    if item.produto_id:
        produto = ('produto', item.produto_id)
    else:
        produto = (
            'livre', (item.descricao or '').strip().casefold(),
            item.modelo_id, (item.referencia or '').strip().casefold(),
        )
    return (*produto, 'ficha', *_ficha_tecnica(item))


def _nome_base(item):
    return item.produto.nome if item.produto_id else (
        item.descricao or 'Item sem descrição'
    )


def agrupar_itens_op(itens) -> list[GrupoItemOP]:
    """Agrupa grades com a mesma ficha, preservando a ordem de entrada."""
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
