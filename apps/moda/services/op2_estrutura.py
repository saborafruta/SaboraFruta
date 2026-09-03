"""Opções de estrutura usadas pela OP 2.0.

Fonte: planilha `produtos-erk-personalizado.xlsx` enviada pelo usuário.
Esses campos ainda não têm colunas próprias no banco; por enquanto entram
como resumo textual nas observações do item para não perder a informação.
"""

from copy import deepcopy

TIPOS_IMPRESSAO_PADRAO = [
    'SUBLIMAÇÃO', 'SILK', 'PLASTISOL', 'BORDADO', 'DTF', 'DTG', 'TRANSFER', 'PATCH', 'RELEVO',
    'SEM IMPRESSÃO', 'OUTRO',
]

CORES_PRINCIPAIS = [
    'PRETO', 'BRANCO', 'AZUL MARINHO', 'AZUL ROYAL', 'VERMELHO', 'VERDE',
    'AMARELO', 'CINZA', 'LARANJA', 'ROSA', 'ROXO', 'BEGE', 'MARROM',
    'COR PERSONALIZADA',
]


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
            'punho': [
                'RETALINEAS', 'DRY', 'REBANA', 'PERSONALIZADOS', 'REBATIMENTO',
                'COM BAINHA', 'SEM COMPRESSÃO', 'COM COMPRESSÃO',
            ],
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
        'label': 'Calção',
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


# Novos tipos comerciais. As variações de camisa e colete mantêm a mesma
# ficha-base; calça e bermuda substituem o tipo composto antigo.
_camisa = OP2_ESTRUTURA_OPCOES['camisa']['campos']
_calca_bermuda = OP2_ESTRUTURA_OPCOES['calca_bermuda']['campos']
_colete = OP2_ESTRUTURA_OPCOES['colete']['campos']
for _slug, _label in (
    ('camisa_polo', 'Camisa Polo'),
    ('camisa_regata', 'Camisa Regata'),
    ('camisa_manga_longa', 'Camisa Manga Longa'),
):
    OP2_ESTRUTURA_OPCOES[_slug] = {'label': _label, 'campos': deepcopy(_camisa)}
OP2_ESTRUTURA_OPCOES['conjunto'] = {
    'label': 'Conjunto',
    'campos': deepcopy(_camisa) | {
        'acabamentos': deepcopy(OP2_ESTRUTURA_OPCOES['calcao']['campos']['acabamentos']),
    },
}
OP2_ESTRUTURA_OPCOES['bermuda'] = {
    'label': 'Bermuda', 'campos': deepcopy(_calca_bermuda),
}
OP2_ESTRUTURA_OPCOES['calca'] = {
    'label': 'Calça', 'campos': deepcopy(_calca_bermuda),
}
OP2_ESTRUTURA_OPCOES['colete_dupla_face'] = {
    'label': 'Colete Dupla Face', 'campos': deepcopy(_colete),
}
OP2_ESTRUTURA_OPCOES['avental'] = {
    'label': 'Avental',
    'campos': {
        'malha': deepcopy(_calca_bermuda['malha']),
        'acabamentos': [
            'BOLSO FRENTE', 'BOLSO LATERAL', 'ALÇA', 'TIRA DE AMARRAÇÃO',
            'REGULADOR', 'DEBRUM',
        ],
    },
}
for _tipo_removido in ('short', 'calca_bermuda'):
    OP2_ESTRUTURA_OPCOES.pop(_tipo_removido, None)

TIPOS_PECA_REMOVIDOS = ('short', 'calca_bermuda')


def campo_multisselecao(campo: str) -> bool:
    """Impressão e acabamento aceitam uma ou várias escolhas."""
    return campo == 'tipo_impressao' or campo.startswith('acabamento')


def valores_estrutura_campo(post, campo: str):
    """Lê escolhas repetidas e também o formato legado separado por vírgula."""
    chave = f'estrutura_{campo}'
    brutos = post.getlist(chave) if hasattr(post, 'getlist') else post.get(chave, [])
    if not isinstance(brutos, (list, tuple)):
        brutos = [brutos]
    valores = []
    for bruto in brutos:
        partes = str(bruto or '').replace(' + ', ',').split(',')
        for parte in partes:
            valor = parte.strip()
            if valor and valor not in valores:
                valores.append(valor)
    return valores

# Todo tipo de peça usa a mesma ficha completa. As opções especializadas de
# cada modelo são preservadas e os campos ausentes recebem o catálogo geral.
_catalogo_campos = {}
for _grupo in OP2_ESTRUTURA_OPCOES.values():
    for _campo, _valores in _grupo.get('campos', {}).items():
        _lista = _catalogo_campos.setdefault(_campo, [])
        for _valor in _valores:
            if _valor not in _lista:
                _lista.append(_valor)

_ordem_campos = [
    'tipo_impressao', 'cor', 'malha', 'gola', 'manga', 'punho', 'frisos',
    'galoes', 'recortes', 'vies', 'abertura', 'regatas',
    'acabamentos_abertura_lateral', 'acabamentos_de_cavas',
    'acabamentos_de_golas', 'ombros', 'barra', 'gorro', 'acabamentos',
    'acabamento', 'tamanho', 'etiquetas',
]
_catalogo_campos['tipo_impressao'] = list(TIPOS_IMPRESSAO_PADRAO)
_catalogo_campos['cor'] = list(CORES_PRINCIPAIS)
_catalogo_campos.setdefault('etiquetas', []).append('PERSONALIZADA CLIENTE')

for _grupo in OP2_ESTRUTURA_OPCOES.values():
    _completos = {}
    for _campo in _ordem_campos:
        if _campo not in _catalogo_campos:
            continue
        # A ficha e o catálogo são únicos para todos os tipos. As listas
        # especializadas que originaram o cadastro continuam contribuindo
        # para a união, mas não limitam mais uma peça específica.
        _valores = list(_catalogo_campos[_campo])
        if _campo == 'etiquetas' and 'PERSONALIZADA CLIENTE' not in _valores:
            _valores.append('PERSONALIZADA CLIENTE')
        for _obrigatoria in ('OUTRO',):
            if _obrigatoria not in _valores:
                _valores.append(_obrigatoria)
        _completos[_campo] = ['N/A', *(
            valor for valor in _valores if valor != 'N/A'
        )]
    _grupo['campos'] = _completos


def _ordenar_tipos(grupos):
    """Conjunto e Camisa primeiro; demais tipos em ordem alfabética."""
    prioridade = {'conjunto': 0, 'camisa': 1}
    return dict(sorted(
        grupos.items(),
        key=lambda item: (
            prioridade.get(item[0], 2),
            (item[1].get('label') or item[0]).casefold(),
        ),
    ))


def _na_primeiro(valores):
    """Remove duplicatas preservando a ordem, sempre com N/A no topo."""
    unicos = []
    for valor in valores:
        if valor and valor != 'N/A' and valor not in unicos:
            unicos.append(valor)
    return ['N/A', *unicos]


OP2_ESTRUTURA_OPCOES = _ordenar_tipos(OP2_ESTRUTURA_OPCOES)


def _normalizar_slug(texto: str) -> str:
    return (texto or '').strip().lower().replace(' ', '_')


def sincronizar_opcoes_padrao(filial):
    """Garante que a filial tenha as opções-base da planilha para edição."""
    if filial is None:
        return
    from apps.moda.models import OpcaoEstruturaOP2

    OpcaoEstruturaOP2.objects.for_filial(filial).filter(
        tipo_peca='calcao', tipo_label='Calções',
    ).update(tipo_label='Calção')

    # Versões antigas chegaram a criar linhas compostas apenas por espaços.
    # Repara a própria linha quando a posição ainda corresponde ao padrão,
    # preservando o identificador usado por formulários que já estavam
    # abertos. Se o valor real já existe, a linha vazia é apenas duplicata.
    for opcao in OpcaoEstruturaOP2.objects.for_filial(filial):
        if (opcao.valor or '').strip():
            continue
        valores = (
            OP2_ESTRUTURA_OPCOES.get(opcao.tipo_peca, {})
            .get('campos', {}).get(opcao.campo, [])
        )
        indice = max(int(opcao.ordem or 1) - 1, 0)
        valor = valores[indice] if indice < len(valores) else ''
        if not valor:
            opcao.delete()
            continue
        conflito = OpcaoEstruturaOP2.objects.for_filial(filial).filter(
            tipo_peca=opcao.tipo_peca, campo=opcao.campo, valor=valor,
        ).exclude(pk=opcao.pk).exists()
        if conflito:
            opcao.delete()
        else:
            opcao.valor = valor
            opcao.save(update_fields=['valor', 'updated_at'])

    existentes = set(
        OpcaoEstruturaOP2.objects.for_filial(filial).values_list(
            'tipo_peca', 'campo', 'valor',
        )
    )
    novas = []
    for tipo_peca, grupo in OP2_ESTRUTURA_OPCOES.items():
        tipo_label = grupo.get('label') or tipo_peca.title()
        for campo, valores in grupo.get('campos', {}).items():
            for ordem, valor in enumerate(valores, start=1):
                valor = (valor or '').strip()
                chave = (tipo_peca, campo, valor)
                if not valor or chave in existentes:
                    continue
                existentes.add(chave)
                novas.append(OpcaoEstruturaOP2(
                    filial=filial, tipo_peca=tipo_peca, tipo_label=tipo_label,
                    campo=campo, valor=valor, ordem=ordem, ativo=True,
                ))
    if novas:
        OpcaoEstruturaOP2.objects.bulk_create(novas, ignore_conflicts=True)

    # Tipos criados pela gestão também recebem a ficha técnica completa.
    tipos_cadastrados = {
        linha['tipo_peca']: linha['tipo_label']
        for linha in OpcaoEstruturaOP2.objects.for_filial(filial).values(
            'tipo_peca', 'tipo_label',
        ).distinct()
        if linha['tipo_peca'] not in TIPOS_PECA_REMOVIDOS
    }
    campos_novos = []
    for tipo_peca, tipo_label in tipos_cadastrados.items():
        for campo, valores in OP2_ESTRUTURA_OPCOES['camisa']['campos'].items():
            for ordem, valor in enumerate(valores, start=1):
                chave = (tipo_peca, campo, valor)
                if chave in existentes:
                    continue
                existentes.add(chave)
                campos_novos.append(OpcaoEstruturaOP2(
                    filial=filial, tipo_peca=tipo_peca,
                    tipo_label=tipo_label or tipo_peca.title(), campo=campo,
                    valor=valor, ordem=ordem, ativo=True,
                ))
    if campos_novos:
        OpcaoEstruturaOP2.objects.bulk_create(campos_novos, ignore_conflicts=True)

    # Campos e opções criados pela filial são globais para a ficha da OP.
    # Materializar a união no banco mantém a tela de gestão e o formulário
    # consistentes em todos os tipos, inclusive após novos deploys.
    catalogo_global = {}
    for tipo_peca, campo, valor in existentes:
        if tipo_peca not in TIPOS_PECA_REMOVIDOS:
            catalogo_global.setdefault(campo, set()).add(valor)
    propagadas = []
    for tipo_peca, tipo_label in tipos_cadastrados.items():
        for campo, valores in catalogo_global.items():
            for ordem, valor in enumerate(sorted(valores), start=1):
                chave = (tipo_peca, campo, valor)
                if chave in existentes:
                    continue
                existentes.add(chave)
                propagadas.append(OpcaoEstruturaOP2(
                    filial=filial, tipo_peca=tipo_peca,
                    tipo_label=tipo_label or tipo_peca.title(), campo=campo,
                    valor=valor, ordem=ordem, ativo=True,
                ))
    if propagadas:
        OpcaoEstruturaOP2.objects.bulk_create(propagadas, ignore_conflicts=True)

    # Campos criados pela gestão também precisam das escolhas especiais.
    campos = OpcaoEstruturaOP2.objects.for_filial(filial).values(
        'tipo_peca', 'tipo_label', 'campo',
    ).distinct()
    especiais = []
    for campo in campos:
        for valor, ordem in (('OUTRO', 998), ('N/A', 999)):
            chave = (campo['tipo_peca'], campo['campo'], valor)
            if chave not in existentes:
                existentes.add(chave)
                especiais.append(OpcaoEstruturaOP2(
                    filial=filial, **campo, valor=valor, ordem=ordem, ativo=True,
                ))
    if especiais:
        OpcaoEstruturaOP2.objects.bulk_create(especiais, ignore_conflicts=True)


def validar_estrutura_item(post, grupos):
    """Todos os campos do tipo escolhido exigem uma opção válida, inclusive N/A."""
    tipo = (post.get('estrutura_tipo') or '').strip()
    grupo = grupos.get(tipo)
    if not grupo:
        raise ValueError('Selecione um tipo de peça válido.')
    for campo, opcoes in grupo['campos'].items():
        valores = valores_estrutura_campo(post, campo)
        rotulo = campo.replace('_', ' ').capitalize()
        if not valores:
            raise ValueError(f'{rotulo}: preenchimento obrigatório. Se não se aplica, selecione N/A.')
        if not campo_multisselecao(campo) and len(valores) > 1:
            raise ValueError(f'{rotulo}: selecione somente uma opção para {grupo["label"]}.')
        if any(valor not in opcoes for valor in valores):
            raise ValueError(f'{rotulo}: selecione uma opção válida para {grupo["label"]}.')
        if len(valores) > 1 and 'N/A' in valores:
            raise ValueError(f'{rotulo}: N/A não pode ser combinado com outra opção.')
        if 'OUTRO' in valores and not (post.get(f'estrutura_outro_{campo}') or '').strip():
            raise ValueError(f'{rotulo}: descreva a opção “Outro”.')
        valor = valores[0]
        if campo == 'cor' and valor == 'COR PERSONALIZADA':
            personalizada = (post.get('estrutura_cor_personalizada') or '').strip()
            if not personalizada:
                raise ValueError('Cor personalizada: informe a cor desejada.')


def validar_valor_unitario(valor):
    """Valida preço também nas requisições diretas e nas edições."""
    from decimal import Decimal
    from django.core.exceptions import ValidationError
    from django.forms import DecimalField

    texto = str(valor or '').strip()
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return DecimalField(
            required=True, min_value=Decimal('0'), max_digits=12, decimal_places=2,
        ).clean(texto)
    except ValidationError:
        raise ValueError('Valor unitário: informe zero ou um valor positivo, com até duas casas decimais.')


def opcoes_estrutura_filial(filial, incluir_inativas=False):
    """Devolve as opções editáveis no mesmo formato usado pela OP."""
    if filial is None:
        return _ordenar_tipos(OP2_ESTRUTURA_OPCOES)
    from apps.moda.models import OpcaoEstruturaOP2

    sincronizar_opcoes_padrao(filial)
    todas = OpcaoEstruturaOP2.objects.for_filial(filial).exclude(
        tipo_peca__in=TIPOS_PECA_REMOVIDOS,
    )
    if not todas.exists():
        return _ordenar_tipos(OP2_ESTRUTURA_OPCOES)
    qs = todas
    if not incluir_inativas:
        qs = qs.filter(ativo=True)

    grupos = {}
    for opcao in qs.order_by('tipo_label', 'campo', 'ordem', 'valor'):
        if not (opcao.valor or '').strip():
            continue
        grupo = grupos.setdefault(opcao.tipo_peca, {
            'label': opcao.tipo_label or opcao.tipo_peca.title(),
            'campos': {},
        })
        grupo['campos'].setdefault(opcao.campo, []).append(opcao.valor)
    # A união inclui também opções e campos criados pela própria filial.
    # Assim, uma opção adicionada em Camisa fica imediatamente disponível em
    # Calção, Conjunto e nos demais tipos, sem catálogos divergentes.
    catalogo_filial = {
        campo: list(valores)
        for campo, valores in OP2_ESTRUTURA_OPCOES['camisa']['campos'].items()
    }
    for grupo in grupos.values():
        for campo, valores in grupo['campos'].items():
            catalogo_filial.setdefault(campo, []).extend(valores)
    catalogo_filial = {
        campo: _na_primeiro(valores)
        for campo, valores in catalogo_filial.items()
    }
    for grupo in grupos.values():
        grupo['campos'] = {
            campo: list(valores) for campo, valores in catalogo_filial.items()
        }
    return _ordenar_tipos(grupos)


def estrutura_resumo(post, grupos=None) -> str:
    """Monta um resumo legível das escolhas de estrutura enviadas pelo form."""
    tipo = (post.get('estrutura_tipo') or '').strip()
    grupos = grupos or OP2_ESTRUTURA_OPCOES
    grupo = grupos.get(tipo)
    if not grupo:
        return ''
    linhas = [f"Tipo de peça: {grupo['label']}"]
    for chave in grupo['campos']:
        valores = valores_estrutura_campo(post, chave)
        if valores:
            rotulo = chave.replace('_', ' ').capitalize()
            if chave == 'cor' and valores[0] == 'COR PERSONALIZADA':
                valores = [(post.get('estrutura_cor_personalizada') or '').strip()]
            if 'OUTRO' in valores:
                outro = (post.get(f'estrutura_outro_{chave}') or '').strip()
                valores = [outro if valor == 'OUTRO' else valor for valor in valores]
            valor = ' + '.join(valores)
            linhas.append(f'{rotulo}: {valor}')
            observacao = (post.get(f'estrutura_observacao_{chave}') or '').strip()
            if observacao:
                linhas.append(f'Observação de {rotulo}: {observacao}')
    return '\n'.join(linhas) if len(linhas) > 1 else ''


def juntar_observacoes_item(observacoes: str, post, grupos=None) -> str:
    """Acrescenta a estrutura escolhida às observações do item."""
    partes = [(observacoes or '').strip()]
    estrutura = estrutura_resumo(post, grupos)
    if estrutura:
        partes.append('Estrutura da peça:\n' + estrutura)
    return '\n\n'.join(parte for parte in partes if parte)
