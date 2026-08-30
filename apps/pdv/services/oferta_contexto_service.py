"""Persistência segura da condição comercial escolhida no carrinho do PDV."""


_CAMPOS_DIRETOS = (
    'oferta_tipo', 'oferta_tag', 'oferta_nome', 'oferta_validade',
    'oferta_brindes', 'oferta_brindes_estoque', 'promocao_id', 'faixa_id',
    'kit_categoria_id', 'regra_id', 'brinde_id', 'kit_id', 'tipo_venda',
    'preco_manual', 'brinde_quantidade_gatilho',
    'oferta_componentes_estoque',
)


def contexto_oferta_do_payload(item_dados: dict) -> dict:
    contexto = {
        campo: item_dados.get(campo)
        for campo in _CAMPOS_DIRETOS
        if item_dados.get(campo) not in (None, '', [])
    }
    mapeados = {
        'quantidade_minima_oferta': item_dados.get('_quantidadeMinOferta'),
        'quantidade_exata': item_dados.get('_quantidadeExata'),
        'oferta_selecionada': item_dados.get('_ofertaSelecionada'),
        'preco_original': item_dados.get('_precoOriginal'),
        'preco_tabela': item_dados.get('_precoTabela'),
    }
    contexto.update({chave: valor for chave, valor in mapeados.items() if valor not in (None, '')})
    return contexto


def aplicar_contexto_oferta(payload: dict, item) -> dict:
    contexto = dict(item.oferta_contexto or {})
    payload.update(contexto)
    payload['_quantidadeMinOferta'] = contexto.get('quantidade_minima_oferta')
    payload['_quantidadeExata'] = bool(contexto.get('quantidade_exata'))
    payload['_ofertaSelecionada'] = bool(contexto.get('oferta_selecionada'))
    payload['preco_origem'] = item.preco_origem or contexto.get('oferta_nome') or 'Preço de venda'
    payload['preco_origem_tipo'] = item.preco_origem or contexto.get('oferta_tipo') or 'normal'
    payload['preco_origem_detalhe'] = item.preco_origem_detalhe or ''
    return payload
