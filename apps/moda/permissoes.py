"""
Áreas do vertical e os sete perfis da confecção.

O PROBLEMA: a permissão do sistema é por MÓDULO, e `moda` é um módulo só.
Quem podia ver o vertical podia tudo dentro dele — o cortador editava o
valor do pedido, o vendedor apontava produção. Para os sete perfis pedidos,
a permissão precisa ser mais fina que o módulo.

A SOLUÇÃO reaproveita o que já existe em vez de inventar um sistema
paralelo: cada área vira um módulo próprio (`moda_corte`, `moda_pcp`...) nas
mesmas `Permissao` de sempre, com as mesmas sete ações. A tela de perfis, o
cadastro de usuário e `tem_permissao` continuam funcionando sem saber que
algo mudou.

COMPATIBILIDADE É OBRIGATÓRIA AQUI. Todo perfil que hoje tem `moda` passaria
a não ter `moda_corte` — e este deploy trancaria para fora todo mundo que
usa o vertical. Por isso `Usuario.tem_permissao` trata `moda` como guarda-
chuva: perfil sem linha específica da área cai na permissão do módulo
inteiro. Perfil novo, criado com as áreas, ignora o guarda-chuva.

A ÁREA VEM DO ARQUIVO DA VIEW, não de um atributo repetido em cada classe.
As telas já estão organizadas por área (`views_corte.py`, `views_pcp.py`), e
a tabela abaixo torna isso explícito num lugar só, auditável de uma olhada.
Onde o arquivo não basta, a classe declara `area` e vence.
"""
from __future__ import annotations

# ── As áreas ─────────────────────────────────────────────────────────────
# A chave vira o módulo de permissão com o prefixo `moda_`.
AREAS = {
    'comercial': 'Comercial — clientes, pedidos, orçamentos, arte',
    'pcp': 'PCP — planejamento, ordens, sequenciamento, capacidade',
    'corte': 'Corte — encaixe, enfesto, consumo e perdas',
    'producao': 'Produção — apontamento de operações, quantidade e tempo',
    'qualidade': 'Qualidade — inspeção, aprovação e retrabalho',
    'expedicao': 'Expedição — conferência, separação, embalagem, entrega',
    'indicadores': 'Indicadores — dashboard, alertas, custos e margens',
}

MODULO_UMBRELA = 'moda'


def modulo_da_area(area: str) -> str:
    return f'{MODULO_UMBRELA}_{area}'


MODULOS_DE_AREA = [modulo_da_area(a) for a in AREAS]

# ── Onde cada tela mora ──────────────────────────────────────────────────
# Arquivo da view → área. Um arquivo fora desta tabela fica só com `moda`,
# que é o comportamento antigo: acrescentar uma tela nova nunca fecha uma
# porta por engano, no máximo deixa de estreitá-la.
AREA_POR_MODULO = {
    'apps.moda.views_cadastros': 'comercial',
    'apps.moda.views_apoio': 'comercial',
    'apps.moda.views_ficha': 'pcp',
    'apps.moda.views_roteiro': 'pcp',
    'apps.moda.views_pcp': 'pcp',
    'apps.moda.views_ordem': 'pcp',
    'apps.moda.views_necessidade': 'pcp',
    'apps.moda.views_corte': 'corte',
    'apps.moda.views_encaixe': 'corte',
    'apps.moda.views_terminal': 'producao',
    'apps.moda.views_fluxo': 'producao',
    'apps.moda.views_kanban': 'producao',
    'apps.moda.views_wip': 'producao',
    'apps.moda.views_qualidade': 'qualidade',
    'apps.moda.views_expedicao': 'expedicao',
    'apps.moda.views_dashboard': 'indicadores',
    'apps.moda.views_alertas': 'indicadores',
    # `views.py` (hub, grupos e placeholders) fica de fora de propósito: é a
    # navegação do vertical, e quem entra em qualquer área precisa dela.
}

# Grupo do menu → área, para o hub não oferecer porta que dá 403.
AREA_POR_GRUPO = {
    'comercial': 'comercial',
    'produtos': 'pcp',
    'engenharia': 'pcp',
    'pcp': 'pcp',
    'producao': 'producao',
    'estoque': 'pcp',
    'expedicao': 'expedicao',
    'indicadores': 'indicadores',
}


def area_da_view(view) -> str | None:
    """A área de uma view: o que ela declara, ou a do arquivo dela."""
    declarada = getattr(view, 'area', None)
    if declarada:
        return declarada
    return AREA_POR_MODULO.get(type(view).__module__)


def pode_na_area(usuario, area: str | None, acao: str = 'ver') -> bool:
    """
    Permissão da área, com o guarda-chuva do módulo por trás.

    Sem área declarada, vale a permissão do vertical inteiro — é o caso da
    navegação e de qualquer tela nova ainda não classificada.
    """
    if area is None:
        return usuario.tem_permissao(MODULO_UMBRELA, acao)
    return usuario.tem_permissao(modulo_da_area(area), acao)


# ── Os sete perfis ───────────────────────────────────────────────────────
# Cada linha é (área, ações). A ação `ver` sozinha é consulta; o resto é o
# que aquele posto de trabalho de fato executa.

TODAS = ('ver', 'criar', 'editar', 'excluir', 'cancelar', 'aprovar', 'exportar')
OPERAR = ('ver', 'criar', 'editar')
CONSULTAR = ('ver',)

PERFIS = {
    'Comercial': {
        'descricao': (
            'Vende e acompanha. Cria pedido, orçamento e arte, gera PDF e '
            'manda pelo WhatsApp — e consulta a produção sem apontar nada nela.'
        ),
        'areas': {
            'comercial': TODAS,
            # "Consultar produção": vê onde o pedido está, não mexe.
            'producao': CONSULTAR,
            'pcp': CONSULTAR,
            'expedicao': CONSULTAR,
        },
        # Clientes moram no cadastro geral, não no vertical.
        'modulos': {'cadastros': OPERAR},
    },
    'PCP': {
        'descricao': (
            'Planeja e emite. Sequencia o roteiro, ajusta capacidade, emite '
            'ordem de produção e acompanha o chão de fábrica.'
        ),
        'areas': {
            'pcp': TODAS,
            'producao': OPERAR,
            'corte': CONSULTAR,
            'comercial': CONSULTAR,
            'indicadores': CONSULTAR,
        },
        'modulos': {},
    },
    'Corte': {
        'descricao': (
            'Corta. Registra encaixe, enfesto, consumo e perda — e lê a OP '
            'para saber o que cortar.'
        ),
        'areas': {
            'corte': TODAS,
            # Precisa LER a ordem para cortar; emitir e reprogramar é do PCP.
            'pcp': CONSULTAR,
            'producao': CONSULTAR,
        },
        'modulos': {},
    },
    'Produção': {
        'descricao': (
            'Aponta o que produziu. Operações, quantidade, tempo e rejeição, '
            'nos terminais de setor.'
        ),
        'areas': {
            'producao': OPERAR,
            'pcp': CONSULTAR,
        },
        'modulos': {},
    },
    'Qualidade': {
        'descricao': (
            'Inspeciona e decide. Aprova, reprova ou manda para retrabalho — '
            'e é o único perfil de chão de fábrica que aprova.'
        ),
        'areas': {
            'qualidade': (*OPERAR, 'aprovar', 'cancelar'),
            'producao': CONSULTAR,
            'pcp': CONSULTAR,
        },
        'modulos': {},
    },
    'Expedição': {
        'descricao': (
            'Fecha e despacha. Confere, separa, embala e entrega, com leitura '
            'de código de barras.'
        ),
        'areas': {
            'expedicao': TODAS,
            'comercial': CONSULTAR,
            'producao': CONSULTAR,
        },
        'modulos': {},
    },
    'Gestão': {
        'descricao': (
            'Vê tudo e decide tudo: produção, financeiro, estoque, custos, '
            'margens, indicadores e auditoria.'
        ),
        'areas': {area: TODAS for area in AREAS},
        # As sete áreas cobrem o vertical; financeiro, estoque, custos e
        # auditoria (log do sistema) são módulos do ERP, fora dele. Sem estes,
        # "acesso completo" pararia na porta da confecção.
        'modulos': {
            'financeiro': TODAS,
            'estoque': TODAS,
            'relatorios': TODAS,
            'compras': TODAS,
            'produtos': TODAS,
            'cadastros': TODAS,
            'config': CONSULTAR,
        },
    },
}


def permissoes_do_perfil(nome: str) -> dict[str, tuple]:
    """
    Módulo → ações, já com o guarda-chuva `moda` resolvido.

    O `moda` entra com a UNIÃO das ações das áreas: sem ele, o
    `FilialMiddleware` e a navegação do vertical barrariam o perfil antes de
    qualquer área ser consultada.
    """
    definicao = PERFIS[nome]
    permissoes: dict[str, tuple] = {}

    uniao: set[str] = set()
    for area, acoes in definicao['areas'].items():
        permissoes[modulo_da_area(area)] = tuple(acoes)
        uniao |= set(acoes)

    permissoes[MODULO_UMBRELA] = tuple(a for a in TODAS if a in uniao)
    permissoes.update({m: tuple(a) for m, a in definicao['modulos'].items()})
    return permissoes
