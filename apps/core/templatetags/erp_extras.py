"""Filtros e tags customizados."""
from decimal import Decimal
from typing import Any

from django import template

try:
    from PIL import Image
except Exception:  # pragma: no cover - ambiente sem Pillow
    Image = None

register = template.Library()


@register.filter
def moeda(valor):
    """Formata Decimal como R$ 1.234,56."""
    if valor is None or valor == '':
        return 'R$ 0,00'
    try:
        valor = Decimal(str(valor))
    except Exception:
        return valor
    s = f'{valor:,.2f}'
    # Converte formato EN (1,234.56) para pt-BR (1.234,56)
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {s}'


@register.filter
def quantidade(valor, casas=2):
    """Formata quantidade sem zeros finais desnecessarios."""
    if valor is None:
        return '0'
    try:
        valor = Decimal(str(valor))
    except Exception:
        return valor
    if valor == valor.to_integral_value():
        casas = 0
    fmt = f'{{:,.{casas}f}}'
    s = fmt.format(valor).replace(',', 'X').replace('.', ',').replace('X', '.')
    if casas:
        s = s.rstrip('0').rstrip(',')
    return s


@register.filter
def quantidade_produto(valor, produto=None):
    """Formata estoque como inteiro para unitarios e decimal para granel/fracionado."""
    if valor is None:
        return '0'
    try:
        valor = Decimal(str(valor))
    except Exception:
        return valor
    usa_decimal = False
    if produto is not None:
        usa_decimal = bool(
            getattr(produto, 'vendido_por_peso_granel', False)
            or getattr(produto, 'fracionavel', False)
            or getattr(produto, 'eh_granel', False)
        )
    casas = 2 if usa_decimal else (2 if valor != valor.to_integral_value() else 0)
    fmt = f'{{:,.{casas}f}}'
    return fmt.format(valor).replace(',', 'X').replace('.', ',').replace('X', '.')


@register.filter
def cpf_cnpj(valor):
    """Formata CPF ou CNPJ."""
    if not valor:
        return ''
    v = ''.join(filter(str.isdigit, str(valor)))
    if len(v) == 11:
        return f'{v[:3]}.{v[3:6]}.{v[6:9]}-{v[9:]}'
    if len(v) == 14:
        return f'{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}'
    return valor


@register.filter
def telefone(valor):
    """Formata telefone brasileiro quando houver DDD."""
    if not valor:
        return ''
    v = ''.join(filter(str.isdigit, str(valor)))
    if len(v) == 11:
        return f'({v[:2]}) {v[2:7]}-{v[7:]}'
    if len(v) == 10:
        return f'({v[:2]}) {v[2:6]}-{v[6:]}'
    return valor


@register.filter
def filial_apelido(filial):
    """Retorna um nome curto e operacional para a filial."""
    if not filial:
        return ''

    nome = (
        getattr(filial, 'nome_fantasia', None)
        or getattr(filial, 'razao_social', None)
        or str(filial)
    ).strip()
    cidade = (getattr(filial, 'cidade', None) or '').strip()

    apelido = nome
    for separador in (' — ', ' – ', ' - ', '—', '–'):
        if separador in apelido:
            apelido = apelido.rsplit(separador, 1)[-1].strip()
            break

    if getattr(filial, 'is_matriz', False) and 'matriz' not in apelido.lower():
        apelido = f'Matriz {cidade}'.strip() if cidade else f'Matriz {apelido}'.strip()

    return apelido or cidade or nome


@register.filter
def cep(valor):
    """Formata CEP."""
    if not valor:
        return ''
    v = ''.join(filter(str.isdigit, str(valor)))
    if len(v) == 8:
        return f'{v[:5]}-{v[5:]}'
    return valor


@register.filter
def semaforo_estoque(item):
    """
    Retorna classe CSS semafórica para nível de estoque.
    Espera um dict/obj com `quantidade` e `minimo`.
    """
    try:
        qtd = float(item.get('quantidade', 0) if isinstance(item, dict) else item.quantidade_disponivel or 0)
        minimo = float(item.get('minimo', 0) if isinstance(item, dict) else item.produto.estoque_minimo or 0)
    except Exception:
        return 'bg-gray-100 text-gray-600'

    if minimo > 0 and qtd <= 0:
        return 'bg-red-100 text-red-700'
    if minimo > 0 and qtd < minimo:
        return 'bg-amber-100 text-amber-700'
    return 'bg-emerald-100 text-emerald-700'


@register.filter
def logo_sidebar_classe(imagem):
    """Retorna a classe de layout da sidebar conforme proporcao da imagem."""
    if not imagem or Image is None:
        return ''

    nome_original = getattr(imagem, 'name', '') or ''
    storage = getattr(imagem, 'storage', None)
    if not nome_original or storage is None:
        return ''

    try:
        with storage.open(nome_original, 'rb') as arquivo:
            with Image.open(arquivo) as base:
                largura, altura = base.size
    except Exception:
        return ''

    if not largura or not altura:
        return ''
    proporcao_arquivo = largura / altura
    if proporcao_arquivo >= 2.25:
        return 'sidebar-branch-logo-card--wide'
    if 0.85 <= proporcao_arquivo <= 1.25:
        return 'sidebar-branch-logo-card--square'
    return ''



@register.filter
def dict_get(d, key):
    """Acessa d[key] em templates: {{ meu_dict|dict_get:chave }}"""
    if isinstance(d, dict):
        return d.get(key)
    return None
