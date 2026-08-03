"""
Retorno visível da geocodificação automática (§2).

O `post_save` já preenche a coordenada ao salvar um cadastro com endereço novo
— mas em silêncio. Quem cadastra não fica sabendo se o cliente entrou no mapa,
e descobre semanas depois, olhando um relatório de cobertura, que o endereço
digitado não existe para o provider.

Este módulo traduz o estado da coordenada numa frase. É só leitura: não
geocodifica nada, apenas olha o que o signal deixou no objeto.
"""
from __future__ import annotations


def mensagem_geo(obj):
    """
    `(nivel, texto)` sobre a coordenada do cadastro, ou `None` se não há o que
    dizer.

    Devolve `None` quando o endereço nem chegou a ser tentado (cadastro sem
    cidade, por exemplo, que o geocoder recusa de propósito): avisar nesse
    caso viraria ruído em todo cadastro incompleto, e a tela já cobra os
    campos obrigatórios.
    """
    if not hasattr(obj, 'latitude'):
        return None

    if obj.latitude is not None:
        if getattr(obj, 'geo_fixado', False):
            return ('info', 'Coordenada fixada manualmente — o endereço não a altera.')
        return ('success', 'Endereço localizado no mapa.')

    erro = (getattr(obj, 'geo_erro', '') or '').strip()
    if erro:
        # O motivo do provider, não um "falhou" generico: e a diferenca entre
        # corrigir o endereco hoje e so descobrir o problema num relatorio.
        return ('warning', f'Não foi possível localizar no mapa: {erro}')

    if not (getattr(obj, 'cidade', '') or '').strip():
        return ('warning',
                'Sem cidade preenchida, o endereço não é buscado no mapa — '
                'a consulta devolveria o centro do estado.')

    # Sem coordenada, sem erro e com endereço utilizável: a geocodificação
    # automática está desligada, ou o provider não respondeu a tempo.
    return ('info',
            'O endereço ainda não foi localizado no mapa. '
            'Rode "manage.py geocodificar" para tentar de novo.')
