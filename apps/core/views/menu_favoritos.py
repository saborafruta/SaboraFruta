import json
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.views import View
from django.template.loader import render_to_string

from apps.core.models import Usuario


MAX_FAVORITOS = 50
CAMINHOS_BLOQUEADOS = {'/auth/logout/'}


def normalizar_caminho_favorito(valor):
    if not isinstance(valor, str) or not valor.strip():
        return None
    partes = urlsplit(valor.strip())
    caminho = partes.path
    if partes.scheme or partes.netloc or not caminho.startswith('/'):
        return None
    if len(caminho) > 255 or caminho in CAMINHOS_BLOQUEADOS:
        return None
    if any(ord(caractere) < 32 for caractere in caminho):
        return None
    return caminho


class MenuFavoritosView(LoginRequiredMixin, View):
    def get(self, request):
        # Use the full, permission-filtered menu as the label/URL source. The
        # compact PDV menu intentionally does not contain every submenu link.
        parser = _MenuLinks()
        parser.feed(render_to_string('core/_sidebar.html', request=request))
        favoritos = []
        for path in request.user.menu_favoritos or []:
            if path in parser.links:
                favoritos.append({'caminho': path, 'nome': parser.links[path]})
        return JsonResponse({'itens': favoritos})

    def handle_no_permission(self):
        return JsonResponse(
            {'ok': False, 'erro': 'Sua sessao expirou. Entre novamente.'},
            status=401,
        )

    def post(self, request):
        try:
            dados = json.loads(request.body or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({'ok': False, 'erro': 'Dados invalidos.'}, status=400)

        caminho = normalizar_caminho_favorito(dados.get('caminho'))
        favorito = dados.get('favorito')
        if caminho is None or not isinstance(favorito, bool):
            return JsonResponse({'ok': False, 'erro': 'Favorito invalido.'}, status=400)

        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().get(pk=request.user.pk)
            favoritos = []
            for item in usuario.menu_favoritos or []:
                item_normalizado = normalizar_caminho_favorito(item)
                if item_normalizado and item_normalizado not in favoritos:
                    favoritos.append(item_normalizado)

            if favorito and caminho not in favoritos:
                if len(favoritos) >= MAX_FAVORITOS:
                    return JsonResponse(
                        {'ok': False, 'erro': 'Limite de favoritos atingido.'},
                        status=400,
                    )
                favoritos.append(caminho)
            elif not favorito and caminho in favoritos:
                favoritos.remove(caminho)

            usuario.menu_favoritos = favoritos
            usuario.save(update_fields=['menu_favoritos', 'updated_at'])

        return JsonResponse({'ok': True, 'favoritos': favoritos})


class _MenuLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs = dict(attrs)
            self.current = [normalizar_caminho_favorito(attrs.get('href')), attrs.get('title'), []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[2].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.current is not None:
            path, title, parts = self.current
            label = ' '.join((title or ''.join(parts)).split())
            if path and label:
                self.links.setdefault(path, label)
            self.current = None
