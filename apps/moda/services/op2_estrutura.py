"""Opções de estrutura usadas pela OP 2.0.

Fonte: planilha `produtos-erk-personalizado.xlsx` enviada pelo usuário.
Esses campos ainda não têm colunas próprias no banco; por enquanto entram
como resumo textual nas observações do item para não perder a informação.
"""

TIPOS_IMPRESSAO_PADRAO = [
    'SUBLIMAÇÃO', 'SILK', 'BORDADO', 'DTF', 'DTG', 'TRANSFER', 'PATCH',
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

# O tipo de impressão pertence à estrutura de qualquer peça e deve aparecer
# antes das características físicas. Mantê-lo no padrão também faz com que a
# tela de gestão permita editar/inativar suas opções por tipo de peça.
for _grupo in OP2_ESTRUTURA_OPCOES.values():
    _grupo['campos'] = {
        'tipo_impressao': list(TIPOS_IMPRESSAO_PADRAO),
        'cor': list(CORES_PRINCIPAIS),
        **_grupo.get('campos', {}),
    }
    for _valores in _grupo['campos'].values():
        _valores.append('N/A')


def _normalizar_slug(texto: str) -> str:
    return (texto or '').strip().lower().replace(' ', '_')


def sincronizar_opcoes_padrao(filial):
    """Garante que a filial tenha as opções-base da planilha para edição."""
    if filial is None:
        return
    from apps.moda.models import OpcaoEstruturaOP2

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

    # Campos criados pela gestão também precisam de uma escolha explícita
    # para quando a característica não se aplica ao produto.
    campos = OpcaoEstruturaOP2.objects.for_filial(filial).values(
        'tipo_peca', 'tipo_label', 'campo',
    ).distinct()
    sem_aplicacao = []
    for campo in campos:
        chave = (campo['tipo_peca'], campo['campo'], 'N/A')
        if chave not in existentes:
            existentes.add(chave)
            sem_aplicacao.append(OpcaoEstruturaOP2(
                filial=filial, **campo, valor='N/A', ordem=999, ativo=True,
            ))
    if sem_aplicacao:
        OpcaoEstruturaOP2.objects.bulk_create(sem_aplicacao, ignore_conflicts=True)


def validar_estrutura_item(post, grupos):
    """Todos os campos do tipo escolhido exigem uma opção válida, inclusive N/A."""
    tipo = (post.get('estrutura_tipo') or '').strip()
    grupo = grupos.get(tipo)
    if not grupo:
        raise ValueError('Selecione um tipo de peça válido.')
    for campo, opcoes in grupo['campos'].items():
        valor = (post.get(f'estrutura_{campo}') or '').strip()
        rotulo = campo.replace('_', ' ').capitalize()
        if not valor:
            raise ValueError(f'{rotulo}: preenchimento obrigatório. Se não se aplica, selecione N/A.')
        if valor not in opcoes:
            raise ValueError(f'{rotulo}: selecione uma opção válida para {grupo["label"]}.')
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
            required=True, min_value=Decimal('0.01'), max_digits=12, decimal_places=2,
        ).clean(texto)
    except ValidationError:
        raise ValueError('Valor unitário: informe um valor maior que zero, com até duas casas decimais.')


def opcoes_estrutura_filial(filial, incluir_inativas=False):
    """Devolve as opções editáveis no mesmo formato usado pela OP."""
    if filial is None:
        return OP2_ESTRUTURA_OPCOES
    from apps.moda.models import OpcaoEstruturaOP2

    sincronizar_opcoes_padrao(filial)
    todas = OpcaoEstruturaOP2.objects.for_filial(filial)
    if not todas.exists():
        return OP2_ESTRUTURA_OPCOES
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
    for grupo in grupos.values():
        campos = grupo['campos']
        if 'tipo_impressao' in campos:
            tipo_impressao = campos.pop('tipo_impressao')
            grupo['campos'] = {'tipo_impressao': tipo_impressao, **campos}
    return grupos


def estrutura_resumo(post, grupos=None) -> str:
    """Monta um resumo legível das escolhas de estrutura enviadas pelo form."""
    tipo = (post.get('estrutura_tipo') or '').strip()
    grupos = grupos or OP2_ESTRUTURA_OPCOES
    grupo = grupos.get(tipo)
    if not grupo:
        return ''
    linhas = [f"Tipo de peça: {grupo['label']}"]
    for chave in grupo['campos']:
        valor = (post.get(f'estrutura_{chave}') or '').strip()
        if valor:
            rotulo = chave.replace('_', ' ').capitalize()
            if chave == 'cor' and valor == 'COR PERSONALIZADA':
                valor = (post.get('estrutura_cor_personalizada') or '').strip()
            linhas.append(f'{rotulo}: {valor}')
    return '\n'.join(linhas) if len(linhas) > 1 else ''


def juntar_observacoes_item(observacoes: str, post, grupos=None) -> str:
    """Acrescenta a estrutura escolhida às observações do item."""
    partes = [(observacoes or '').strip()]
    estrutura = estrutura_resumo(post, grupos)
    if estrutura:
        partes.append('Estrutura da peça:\n' + estrutura)
    return '\n\n'.join(parte for parte in partes if parte)
