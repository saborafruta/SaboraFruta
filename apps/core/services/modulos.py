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
    Lista para a tela da Central Administrativa: só os módulos que a
    empresa pode ter, já com o motivo de estarem ali. Um módulo de outro
    vertical não aparece nem como caixa desmarcada -- mostrar sugeriria
    que basta marcar, e não basta: depende do segmento.
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
