"""Opções de estrutura usadas pela OP 2.0.

Fonte: planilha `produtos-erk-personalizado.xlsx` enviada pelo usuário.
Esses campos ainda não têm colunas próprias no banco; por enquanto entram
como resumo textual nas observações do item para não perder a informação.
"""

OP2_ESTRUTURA_OPCOES = {
    'camisa': {
        'label': 'Camisa',
        'campos': {
            'malha': [
                'PP', 'PV', 'HELANQUINHA', 'DRYTECH', 'DRY TEXTURIZADO 3D',
                'G. ARROZ', 'COLMEIA', 'ACTIVE AIR', 'POLIAMIDA UV 50+ GRAMA LEVE',
                'POLIAMIDA UV 50+ COM ELASTANO', 'PIQUET', 'ALGODAO PENTEADO 30',
                'SUEDINE', 'PA POLIESTER', 'PA POLIAMINA', 'HENQUINHA DRY',
                'MICROFIBRA UV COM ELASTANO', 'TELAS', 'RGB', 'MIAMI',
            ],
            'gola': [
                'T-SHIRT BASICA', 'POLO', 'REGATA COLEGIAL', 'REGATA MACHAO',
                'PORTUGUESA', 'FRADE', 'V', 'MIAMI', 'VIES',
            ],
            'manga': ['CURTA', 'LONGA', 'HARLAN CURVADA', 'HAGLAN RETA', 'REBATIMENTO'],
            'punho': ['RETALINEAS', 'DRY', 'REBANA', 'PERSONALIZADOS', 'REBATIMENTO'],
            'frisos': [
                'FRIZO ENTRE PUNHOS', 'FRIZO GOLA', 'FRIZO RECORTE',
                'FRIZO EM CAVAS DE MANGAS', 'FRIZO EM OMBROS',
                'MAIS DE UMA OPÇÃO DE FRIZO',
            ],
            'galoes': ['ENTRE PUNHOS', 'GOLA', 'RECORTE', 'CAVAS DE MANGAS'],
            'recortes': ['LATERAIS', 'OMBROS', 'COSTAS', 'FRENTE', 'REBATIMENTO'],
            'vies': ['MANGAS', 'CAVA', 'PESCOÇO'],
            'abertura': ['ZIPER 10 CM', '3 BOTOES', '2 BOTOES'],
            'regatas': ['BASQUETEIRA', 'REGATA COLEGIAL', 'REGATA NADADOR', 'REGATA MACHAO'],
            'acabamentos_abertura_lateral': [
                'ABERTURA LATERAL', 'ABERTURA LATERAL COM DEBRUM',
            ],
            'acabamentos_de_cavas': ['OMBRO A OMBRO'],
            'acabamentos_de_golas': ['VIES INTERNO', 'REBATIMENTO'],
            'ombros': ['REBATIMENTO'],
            'etiquetas': [
                'SILK', 'DTF', 'FITA', 'TEXTIL', 'ASSINATURA ERK PEITO',
                'ASSINATURA ERK BARRA DA FRENTE', 'PRODUTO OFICIAL',
            ],
        },
    },
    'agasalho': {
        'label': 'Agasalho',
        'campos': {
            'malha': ['MOLETOM', 'MOLETINHO', 'HELANCA DUBLADA', 'HELANCA DRY', 'POLIAMIDA', 'TACTEL'],
            'gola': ['FRADE', 'ALTA'],
            'barra': ['RETILINEA', 'REBANA'],
            'punho': ['RETILINEA', 'REBANA'],
            'gorro': ['GORRO FORRADO'],
            'frisos': ['FRIZOS', 'FRIZO ENTRE PUNHOS', 'FRIZO GOLA', 'FRIZO RECORTE', 'FRIZO EM CAVAS DE MANGAS', 'FRIZO EM OMBROS'],
            'galoes': ['GALOES', 'ENTRE PUNHOS', 'GOLA', 'RECORTE', 'CAVAS DE MANGAS'],
            'recortes': ['RECORTES', 'LATERAIS', 'OMBROS', 'COSTAS', 'FRENTE', 'REBATIMENTO'],
            'vies': ['MANGAS', 'CAVA', 'PESCOÇO'],
            'abertura': ['ZIPER 15 CM', 'ZIPER TOTAL'],
            'etiquetas': ['SILK', 'DTF', 'FITA', 'TEXTIL', 'ASSINATURA ERK PEITO', 'ASSINATURA ERK BARRA DA FRENTE', 'PRODUTO OFICIAL'],
        },
    },
    'calcao': {
        'label': 'Calções',
        'campos': {
            'malha': ['HELANQUINHA', 'POLISTER 100 SUBLIMADO', 'DRY', 'DRY TEXTURIZADO'],
            'acabamentos': ['RECORTE', 'APLICACAO DE GALAO', 'CADARÇO', 'FRIZOS', 'FORRO', 'SUBLIMAÇÃO', 'PET APLICADO'],
            'etiquetas': ['SILK', 'DTF', 'FITA', 'TEXTIL', 'ASSINATURA ERK PEITO', 'ASSINATURA ERK BARRA DA FRENTE', 'PRODUTO OFICIAL'],
        },
    },
    'calca_bermuda': {
        'label': 'Calça / bermudas',
        'campos': {
            'malha': ['MOLETOM', 'TACTEL', 'CHIMPA', 'GABARDINE', 'OXFORD', 'OXFORDINE', 'HELANCA DRY', 'TWO WEY', 'HELANCA POLIESTE', 'HELANCA POLIAMIDA', 'BRIM'],
            'acabamentos': ['BOLSO FRENTE', 'BOLSO LATERAL', 'BOLSO TRASEIRO', 'BOLSO ZIPER', 'BOLSO FACA', 'BOLSO CARGO'],
        },
    },
    'short': {
        'label': 'Short',
        'campos': {
            'malha': ['HELANQUINHA', 'POLISTER 100 SUBLIMADO', 'DRY', 'DRY TEXTURIZADO', 'SUPLEX POLIAMINA', 'SUPLEX POLIESTE', 'HELANCA', 'SHORT SAIA'],
            'acabamento': ['RECORTE', 'GALAO', 'ELASTICO', 'COS DE ELASTICO'],
        },
    },
    'colete': {
        'label': 'Colete',
        'campos': {
            'malha': ['PP', 'PV', 'HELANQUINHA', 'DRYTECH', 'DRY TEXTURIZADO 3D', 'G. ARROZ', 'COLMEIA', 'ACTIVE AIR', 'PA POLIESTER', 'PA POLIAMINA', 'HENQUINHA DRY', 'MICROFIBRA UV COM ELASTANO', 'TACTEL', 'OXFORD', 'TELAS', 'RGB'],
        },
    },
    'bandeira': {'label': 'Bandeira', 'campos': {'malha': ['OXFORD', 'TACTEL', 'GABARDINE'], 'tamanho': ['150X100']}},
    'manguitos': {'label': 'Manguitos', 'campos': {'malha': ['MICROFIBRA UV', 'ACTIVE AIR', 'POLIAMIDA UV', '100 SUBLIMADO', 'SILK PRENSADO']}},
    'ecobag': {'label': 'Ecobag', 'campos': {'malha': ['ALGODAO CRU', 'ALGODAO CRU SINTETICO', 'OXFORD', 'GABARDINE', 'TACTEL', 'TNT'], 'acabamentos': ['SILK', 'SUBLIMADO', 'ALÇA PERSONALIZADA', 'ALÇA FAB.']}},
}


def estrutura_resumo(post) -> str:
    """Monta um resumo legível das escolhas de estrutura enviadas pelo form."""
    tipo = (post.get('estrutura_tipo') or '').strip()
    grupos = OP2_ESTRUTURA_OPCOES
    grupo = grupos.get(tipo)
    if not grupo:
        return ''
    linhas = [f"Tipo de peça: {grupo['label']}"]
    for chave in grupo['campos']:
        valor = (post.get(f'estrutura_{chave}') or '').strip()
        if valor:
            rotulo = chave.replace('_', ' ').capitalize()
            linhas.append(f'{rotulo}: {valor}')
    return '\n'.join(linhas) if len(linhas) > 1 else ''


def juntar_observacoes_item(observacoes: str, post) -> str:
    """Acrescenta a estrutura escolhida às observações do item."""
    partes = [(observacoes or '').strip()]
    estrutura = estrutura_resumo(post)
    if estrutura:
        partes.append('Estrutura da peça:\n' + estrutura)
    return '\n\n'.join(parte for parte in partes if parte)
