"""
Áreas do vertical Polpa de Frutas.

MESMA SOLUÇÃO DO VERTICAL DE MODA, de propósito: cada área vira um módulo
de permissão (`polpa_recebimento`, `polpa_frio`...) nas `Permissao` que já
existem, com as mesmas sete ações. A tela de perfis, o cadastro de usuário
e `tem_permissao` continuam funcionando sem saber que algo mudou -- e quem
já entendeu a permissão da confecção não precisa aprender outra coisa aqui.

POR QUE NÃO BASTA UM MÓDULO SÓ. Quem está na balança recebendo fruta às 5h
da manhã não é quem libera um lote na qualidade, e não deveria poder. Sem a
área, entrar no vertical daria acesso a tudo dentro dele -- inclusive
aprovar o próprio recebimento, que é justamente o controle que a indústria
de alimento precisa manter separado.

A ÁREA VEM DO ARQUIVO DA VIEW. As telas são organizadas por área
(`views_recebimento.py`), e a tabela abaixo torna isso explícito num lugar
auditável. Onde o arquivo não basta, a classe declara `area` e vence.
"""
from __future__ import annotations

# ── As áreas ─────────────────────────────────────────────────────────────
AREAS = {
    'recebimento': 'Recebimento — balança, classificação da fruta e produtores',
    'formulacao': 'Formulação — produtos, receitas, rendimento e embalagem',
    'producao': 'Produção — ordens, batidas, etapas e perdas',
    'frio': 'Cadeia de frio — câmaras, túnel, temperatura e estoque congelado',
    'qualidade': 'Qualidade — análises, laudos, não conformidades e rastreio',
    'expedicao': 'Expedição — separação FEFO, carregamento e entrega',
    'indicadores': 'Indicadores — painel, rendimento, custo e validade',
}

MODULO_UMBRELA = 'polpa'


def modulo_da_area(area: str) -> str:
    return f'{MODULO_UMBRELA}_{area}'


MODULOS_DE_AREA = [modulo_da_area(a) for a in AREAS]

# Arquivo da view → área. Fora desta tabela, a tela fica só com `polpa`:
# acrescentar uma tela nova nunca fecha uma porta por engano, no máximo
# deixa de estreitá-la.
AREA_POR_MODULO = {
    'apps.polpa.views_recebimento': 'recebimento',
    'apps.polpa.views_planejamento': 'producao',
    # `views.py` (hub, grupos e telas em construção) fica de fora de
    # propósito: é a navegação do vertical, e quem entra em qualquer área
    # precisa dela.
}

# Grupo do menu → área, para o hub não oferecer porta que dá 403.
AREA_POR_GRUPO = {
    'recebimento': 'recebimento',
    # O PCP é a mesma área da produção: quem planeja o dia é quem toca o
    # dia. Uma área própria daria um perfil a mais para administrar sem
    # nenhum posto de trabalho correspondente na fábrica.
    'pcp': 'producao',
    'formulacao': 'formulacao',
    'producao': 'producao',
    'frio': 'frio',
    'qualidade': 'qualidade',
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

    Perfil que tem `polpa` e não tem a linha da área continua entrando --
    é o que evita que ligar as áreas tranque para fora quem já usava o
    vertical. Perfil novo, criado com as áreas, ignora o guarda-chuva.
    """
    if area is None:
        return usuario.tem_permissao(MODULO_UMBRELA, acao)
    return usuario.tem_permissao(modulo_da_area(area), acao)
