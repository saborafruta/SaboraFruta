from django import template


register = template.Library()


@register.filter
def dict_get(valor, chave):
    return (valor or {}).get(chave, {})


@register.filter
def campo_op2_label(campo):
    """Transforma a chave interna da estrutura em um rótulo legível."""
    if campo == 'tipo_impressao':
        return 'Tipo de impressão'
    return str(campo or '').replace('_', ' ').capitalize()
