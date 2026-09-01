import json
import math

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.views import View

from apps.core.models import Usuario


MAX_TABELAS = 100
MAX_COLUNAS = 64
MAX_LARGURA = 2400


def normalizar_chave_tabela(valor):
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    if not valor or len(valor) > 255 or not valor.startswith('/'):
        return None
    if any(ord(caractere) < 32 for caractere in valor):
        return None
    return valor


def normalizar_preferencia_tabela(valor):
    if not isinstance(valor, dict):
        return None

    ocultas = valor.get('hidden', [])
    larguras = valor.get('widths', {})
    if not isinstance(ocultas, list) or not isinstance(larguras, dict):
        return None

    hidden = []
    for coluna in ocultas:
        if not isinstance(coluna, str) or not coluna or len(coluna) > 80:
            return None
        if coluna not in hidden:
            hidden.append(coluna)
        if len(hidden) > MAX_COLUNAS:
            return None

    widths = {}
    for coluna, largura in larguras.items():
        if not isinstance(coluna, str) or not coluna or len(coluna) > 80:
            return None
        if (
            isinstance(largura, bool)
            or not isinstance(largura, (int, float))
            or not math.isfinite(largura)
        ):
            return None
        widths[coluna] = max(48, min(MAX_LARGURA, round(largura)))
        if len(widths) > MAX_COLUNAS:
            return None

    return {'hidden': hidden, 'widths': widths}


class TabelaPreferenciasView(LoginRequiredMixin, View):
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

        chave = normalizar_chave_tabela(dados.get('table'))
        preferencia = normalizar_preferencia_tabela(dados.get('preferences'))
        if chave is None or preferencia is None:
            return JsonResponse({'ok': False, 'erro': 'Preferencia invalida.'}, status=400)

        with transaction.atomic():
            usuario = Usuario.objects.select_for_update().get(pk=request.user.pk)
            preferencias = dict(usuario.preferencias_tabelas or {})
            if chave not in preferencias and len(preferencias) >= MAX_TABELAS:
                return JsonResponse(
                    {'ok': False, 'erro': 'Limite de tabelas personalizadas atingido.'},
                    status=400,
                )
            preferencias[chave] = preferencia
            usuario.preferencias_tabelas = preferencias
            usuario.save(update_fields=['preferencias_tabelas', 'updated_at'])

        return JsonResponse({'ok': True, 'table': chave, 'preferences': preferencia})
