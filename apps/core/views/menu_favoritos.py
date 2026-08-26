import json
from urllib.parse import urlsplit

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.views import View

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
