import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe


register = template.Library()

URL_RE = re.compile(r'https?://[^\s<>]+', re.IGNORECASE)
FORMATOS = (
    (re.compile(r'`([^`\n]+)`'), 'code'),
    (re.compile(r'(?<!\w)\*([^*\n]+)\*'), 'strong'),
    (re.compile(r'(?<!\w)_([^_\n]+)_'), 'em'),
    (re.compile(r'(?<!\w)~([^~\n]+)~'), 'del'),
)


def formatar_texto_whatsapp(valor):
    texto = str(valor or '')
    links = []

    def guardar_link(match):
        url = match.group(0)
        final = ''
        while url and url[-1] in '.,;:!?)]}':
            final = url[-1] + final
            url = url[:-1]
        indice = len(links)
        links.append(
            f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
            f'{escape(url)}</a>'
        )
        return f'\x00LINK{indice}\x00{final}'

    seguro = escape(URL_RE.sub(guardar_link, texto))
    for padrao, tag in FORMATOS:
        seguro = padrao.sub(rf'<{tag}>\1</{tag}>', seguro)
    seguro = seguro.replace('\n', '<br>')
    for indice, link in enumerate(links):
        seguro = seguro.replace(f'\x00LINK{indice}\x00', link)
    return mark_safe(seguro)


@register.filter
def whatsapp_text(valor):
    return formatar_texto_whatsapp(valor)


@register.filter
def whatsapp_status(valor):
    status = str(valor or '').lower()
    if status in {'lida', 'read', 'played'}:
        return {'icone': '✓✓', 'classe': 'read', 'rotulo': 'Lida'}
    if status in {'entregue', 'delivery_ack', 'delivered'}:
        return {'icone': '✓✓', 'classe': 'delivered', 'rotulo': 'Entregue'}
    if status in {'enviada', 'enviado', 'server_ack', 'sent'}:
        return {'icone': '✓', 'classe': 'sent', 'rotulo': 'Enviada'}
    if status in {'pendente', 'pending'}:
        return {'icone': '◷', 'classe': 'pending', 'rotulo': 'Enviando'}
    if status in {'erro', 'error'}:
        return {'icone': '!', 'classe': 'error', 'rotulo': 'Falha no envio'}
    return {'icone': '', 'classe': '', 'rotulo': status}
