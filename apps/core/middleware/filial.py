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

from apps.core.models import EmpresaBanco, Filial
from apps.core.services.modulos import modulo_da_url, modulos_ativos
from apps.core.tenant_registry import register_tenant_database


class FilialMiddleware:
    """Injeta `request.filial_ativa` em toda request autenticada."""

    # URLs que não exigem filial definida (login, logout, troca de filial)
    EXEMPT_URLS = (
        '/comprovante/',
        '/auth/login/',
        '/auth/logout/',
        '/auth/minha-foto/',
        '/auth/trocar-filial/',
        '/gestao/central/',
        '/gestao/empresas/',
        '/gestao/filiais/',
        '/gestao/separacoes-filiais/',
        '/admin/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _filial_central_do_tenant(request, filial_id):
        """Traduz a filial do tenant para a copia do diretorio central.

        Retorna ``(filial, tentou_mapear)``. Quando existe tenant selecionado,
        uma falha de mapeamento nunca pode cair no lookup comum por PK, pois
        esse mesmo numero pode identificar outra empresa no Banco Gerencial.
        """
        alias = getattr(request, 'selected_tenant_db_alias', None)
        if not alias or getattr(request, 'tenant_db_alias', None):
            return None, False
        try:
            banco = EmpresaBanco.objects.using('default').get(
                db_alias=alias,
                ativo=True,
                status=EmpresaBanco.Status.ATIVO,
            )
            if not register_tenant_database(banco):
                return None, True
            filial_tenant = Filial.objects.using(alias).get(pk=filial_id, ativo=True)
            filial_central = (
                Filial.objects.using('default')
                .select_related('empresa')
                .get(
                    empresa_id=banco.empresa_id,
                    cnpj=filial_tenant.cnpj,
                    ativo=True,
                )
            )
            return filial_central, True
        except (EmpresaBanco.DoesNotExist, Filial.DoesNotExist):
            return None, True

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

        filial, tentou_mapear = self._filial_central_do_tenant(request, filial_id)
        try:
            if tentou_mapear and filial is None:
                raise Filial.DoesNotExist
            if not tentou_mapear:
                filial = Filial.objects.select_related('empresa').get(
                    pk=filial_id, ativo=True,
                )
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
