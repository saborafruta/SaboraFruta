"""
Resolve quais módulos uma empresa/filial enxerga.

Existe para que middleware, sidebar e Central Administrativa respondam a
mesma pergunta do mesmo jeito. Quando essa regra estava repetida em cada
lugar, escondia-se o item do menu mas a URL continuava acessível.
"""
from apps.core.constants.modulos import MODULOS, MODULOS_POR_CHAVE


def modulos_disponiveis(empresa) -> set[str]:
    """
    O que o vertical da empresa concede: universais + os do segmento +
    os liberados na mão pelo admin (`modulos_extras`).

    Sem empresa (superusuário em seleção global), devolve só os universais
    -- é o conjunto seguro, porque nenhum vertical foi escolhido ainda.
    """
    disponiveis = {m.chave for m in MODULOS if m.e_universal}
    if empresa is None:
        return disponiveis

    segmento = getattr(empresa, 'segmento', '') or ''
    if segmento:
        disponiveis |= {m.chave for m in MODULOS if segmento in m.segmentos}

    # Ignora chave desconhecida em modulos_extras: valor antigo de um módulo
    # renomeado não pode liberar nada nem quebrar a tela.
    extras = getattr(empresa, 'modulos_extras', None) or []
    disponiveis |= {c for c in extras if c in MODULOS_POR_CHAVE}
    return disponiveis


def modulos_ativos(filial) -> set[str]:
    """
    O que a filial realmente usa: o disponível menos o que ela desligou.

    Desligar na filial nunca libera nada -- só tira. Um módulo fora do
    segmento continua invisível mesmo que alguém o retire da lista de
    desativados.
    """
    if filial is None:
        return modulos_disponiveis(None)

    disponiveis = modulos_disponiveis(getattr(filial, 'empresa', None))
    desativados = set(getattr(filial, 'modulos_desativados', None) or [])
    return disponiveis - desativados


def modulo_da_url(caminho: str) -> str | None:
    """Chave do módulo que cobre esse caminho, ou None se não for de nenhum."""
    for modulo in MODULOS:
        if any(caminho.startswith(p) for p in modulo.prefixos):
            return modulo.chave
    return None


def modulos_para_admin(empresa) -> list[dict]:
    """
    Lista para a Central Administrativa: os módulos que a empresa TEM.

    Só o que está disponível — o que ela não tem aparece em
    `modulos_de_verticais`, que é uma pergunta diferente ("o que eu poderia
    ligar?") e merece um lugar próprio na tela.
    """
    disponiveis = modulos_disponiveis(empresa)
    segmento = getattr(empresa, 'segmento', '') or ''
    return [
        {
            'chave': m.chave,
            'label': m.label,
            'descricao': m.descricao,
            # Distingue "veio do vertical" de "é de todo mundo", para a tela
            # poder explicar por que aquele módulo está disponível.
            'do_segmento': bool(segmento and segmento in m.segmentos),
        }
        for m in MODULOS
        if m.chave in disponiveis
    ]


def modulos_de_verticais(empresa) -> list[dict]:
    """
    Os módulos especializados que existem no sistema, com a situação de cada
    um para esta empresa.

    Antes eu escondia da tela o que a empresa não tinha. Isso deixava um
    vertical inteiro invisível: quem não soubesse que o módulo Moda existe
    não tinha como descobrir, e nem por que o menu não aparecia. Listar
    tudo, dizendo de onde vem cada um, resolve — e é aqui que o admin liga
    um módulo fora do segmento (`Empresa.modulos_extras`), que é a
    habilitação manual.
    """
    segmento = getattr(empresa, 'segmento', '') or ''
    extras = set(getattr(empresa, 'modulos_extras', None) or [])
    return [
        {
            'chave': m.chave,
            'label': m.label,
            'descricao': m.descricao,
            'segmentos': m.segmentos,
            'pelo_segmento': bool(segmento and segmento in m.segmentos),
            'manual': m.chave in extras,
        }
        for m in MODULOS
        if not m.e_universal
    ]


# Verticais que trazem a PRÓPRIA produção. Onde um deles está ativo, a
# tela genérica de Ordens de Produção (`/producao/`) vira duplicata: a
# fábrica aponta no PCP do vertical, e a lista genérica fica sempre
# vazia -- pior do que não existir, porque quem clica conclui que o
# sistema perdeu as ordens.
#
# A regra fica aqui, e não escondida numa condição do sidebar, para ser
# uma linha de leitura só: o próximo vertical com produção própria entra
# nesta lista e mais nada muda.
VERTICAIS_COM_PRODUCAO_PROPRIA = ('moda',)


def producao_generica_visivel(filial) -> bool:
    """
    A tela genérica de produção deve aparecer no menu desta filial?

    Some só para quem tem um vertical que já resolve produção. Para todo
    o resto do ERP -- indústria alimentícia, polpas, padaria -- ela
    continua sendo A tela de produção, e retirá-la seria tirar o módulo
    de quem o usa.
    """
    ativos = modulos_ativos(filial)
    if 'operacoes' not in ativos:
        return False
    return not any(v in ativos for v in VERTICAIS_COM_PRODUCAO_PROPRIA)