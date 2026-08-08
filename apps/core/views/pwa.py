"""
Service worker servido da raiz do site.

Precisa ser em `/sw.js`, e não em `/static/sw.js`: o navegador limita o
escopo de um service worker ao diretório de onde ele foi baixado, então
servido de `/static/` ele só controlaria `/static/` -- e nao o app.

Sem login de propósito: o arquivo é o mesmo para todo mundo e o navegador
busca o service worker fora do contexto da página (uma resposta de
redirect para o login quebraria o registro).
"""
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.views.decorators.cache import cache_control


def _caminho_sw() -> Path | None:
    """Procura o sw.js onde ele pode estar em dev e em produção."""
    candidatos = [
        Path(settings.BASE_DIR) / 'static' / 'sw.js',      # dev
        Path(settings.STATIC_ROOT or '') / 'sw.js',        # após collectstatic
    ]
    for caminho in candidatos:
        if caminho.is_file():
            return caminho
    return None


# max-age curto: o navegador precisa notar uma versão nova do worker sem
# ficar semanas preso na antiga.
@cache_control(max_age=0, no_cache=True, must_revalidate=True)
def service_worker(request):
    caminho = _caminho_sw()
    if caminho is None:
        raise Http404('sw.js não encontrado.')
    resposta = FileResponse(caminho.open('rb'), content_type='application/javascript')
    # Autoriza o escopo raiz mesmo se o arquivo passar a ser servido de
    # outro diretório mais tarde.
    resposta['Service-Worker-Allowed'] = '/'
    return resposta
