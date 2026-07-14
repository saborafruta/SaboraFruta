"""Decorator e mixin para controle de permissao por modulo/acao."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import redirect


PERMISSION_DENIED_MESSAGE = 'Você não tem permissão para esta ação.'

_API_MARKERS = ('/api/', '/pdv/api/', '/financeiro/api/', '/estoque/api/')


def _is_api_request(request) -> bool:
    return (
        any(marker in request.path for marker in _API_MARKERS)
        or request.headers.get('Content-Type', '').startswith('application/json')
        or request.headers.get('Accept', '').startswith('application/json')
    )


def requer_permissao(modulo: str, acao: str = 'ver'):
    """
    Decorator para views baseadas em funcao.

    Uso:
        @requer_permissao('produtos', 'criar')
        def produto_create(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if _is_api_request(request):
                    return JsonResponse(
                        {"erro": "Sessão expirada. Recarregue a página e faça login novamente."},
                        status=401,
                    )
                return redirect_to_login(request.get_full_path())
            if not request.user.tem_permissao(modulo, acao):
                if _is_api_request(request):
                    return JsonResponse({"erro": PERMISSION_DENIED_MESSAGE}, status=403)
                messages.error(request, PERMISSION_DENIED_MESSAGE)
                return redirect('core:dashboard')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


class PermissaoRequiredMixin:
    """
    Mixin para views baseadas em classe.

    Uso:
        class ProdutoCreateView(PermissaoRequiredMixin, View):
            permissao_modulo = 'produtos'
            permissao_acao = 'criar'
    """
    permissao_modulo: str | None = None
    permissao_acao: str = 'ver'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('core:login')
        if self.permissao_modulo and not request.user.tem_permissao(
            self.permissao_modulo, self.permissao_acao,
        ):
            messages.error(request, PERMISSION_DENIED_MESSAGE)
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
