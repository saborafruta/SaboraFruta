from django import template
from django.template.loader import render_to_string

from apps.core.views.menu_favoritos import resolver_favoritos

register = template.Library()


@register.simple_tag(takes_context=True)
def favoritos_menu_completo(context):
    """Resolve PDV favorites with the same context/permissions as the full menu.

    Reuse the existing context so processors (including database queries) are
    not executed a second time. The rendered sidebar is parsed, never embedded.
    """
    caminhos = context['request'].user.menu_favoritos
    if not caminhos:
        return []
    html = render_to_string('core/_sidebar.html', context.flatten())
    return resolver_favoritos(caminhos, html)
