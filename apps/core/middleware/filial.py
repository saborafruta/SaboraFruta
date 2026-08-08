"""
Middleware que resolve a filial ativa da request.

Regras:
1. Se o usuário está autenticado, tenta recuperar `filial_ativa_id` da sessão.
2. Se não houver na sessão, usa `usuario.filial` (filial padrão).
3. Se o usuário não pertence à filial informada, retorna 403.
4. Injeta `request.filial_ativa` em toda request autenticada.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.constants.modulos import PREFIXOS_POR_MODULO
from apps.core.models import Filial


class FilialMiddleware:
    """Injeta `request.filial_ativa` em toda request autenticada."""

    # URLs que não exigem filial definida (login, logout, troca de filial)
    EXEMPT_URLS = (
        '/auth/login/',
        '/auth/logout/',
        '/auth/minha-foto/',
        '/auth/trocar-filial/',
        '/gestao/central/',
        '/gestao/empresas/',
        '/gestao/filiais/',
        '/admin/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.filial_ativa = None

        # Ignora URLs isentas
        if any(request.path.startswith(url) for url in self.EXEMPT_URLS):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        # Resolve filial ativa
        filial_id = request.session.get('filial_ativa_id') or request.user.filial_id

        if not filial_id and request.user.is_superuser and request.GET.get('central_filial') and (
            request.path.startswith('/gestao/usuarios/') or request.path.startswith('/gestao/perfis/')
        ):
            return self.get_response(request)

        if not filial_id:
            # Usuário sem filial definida — redireciona para seleção
            if request.path != reverse('core:selecionar-filial'):
                return redirect('core:selecionar-filial')
            return self.get_response(request)

        try:
            filial = Filial.objects.select_related('empresa').get(pk=filial_id, ativo=True)
        except Filial.DoesNotExist:
            request.session.pop('filial_ativa_id', None)
            return redirect('core:selecionar-filial')

        # Valida se o usuário pode acessar essa filial
        if not request.user.pode_acessar_filial(filial):
            request.session.pop('filial_ativa_id', None)
            return redirect('core:selecionar-filial')

        request.filial_ativa = filial
        request.user._perfil_ativo = request.user.perfil_para_filial(filial)

        # Bloqueia acesso direto a uma secao de menu desativada nesta filial
        # (alem de escondida no sidebar) -- superuser nunca fica trancado
        # fora, pra sempre poder religar pela Central Administrativa.
        if filial.modulos_desativados and not request.user.is_superuser:
            for chave, prefixos in PREFIXOS_POR_MODULO.items():
                if chave in filial.modulos_desativados and any(
                    request.path.startswith(p) for p in prefixos
                ):
                    messages.error(request, 'Este módulo está desativado para esta filial.')
                    return redirect('core:dashboard')

        return self.get_response(request)
