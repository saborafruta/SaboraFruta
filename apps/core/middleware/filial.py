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

from apps.core.models import Filial
from apps.core.services.modulos import modulo_da_url, modulos_ativos


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

        # Bloqueia acesso direto a modulo que a filial nao tem -- seja
        # porque desligou, seja porque o vertical da empresa nao concede.
        # Esconder do menu nao basta: a URL continuaria respondendo.
        # Superuser nunca fica trancado fora, pra sempre poder ajustar pela
        # Central Administrativa.
        if not request.user.is_superuser:
            chave = modulo_da_url(request.path)
            if chave and chave not in modulos_ativos(filial):
                messages.error(
                    request, 'Este módulo não está disponível para esta filial.'
                )
                return redirect('core:dashboard')

        return self.get_response(request)
