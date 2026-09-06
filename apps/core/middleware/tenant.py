"""Prepara o contexto do banco da empresa ativa."""
import logging

from django.conf import settings
from django.db import connections
from django.http import HttpResponseRedirect, JsonResponse
from django.core.exceptions import ImproperlyConfigured

from apps.core.models import EmpresaBanco
from apps.core.tenant_context import reset_current_tenant_db, set_current_tenant_db
from apps.core.tenant_registry import register_tenant_database
from apps.core.services.tenant_public_link_service import TenantPublicLinkService


logger = logging.getLogger(__name__)
AUTH_DATABASE_SESSION_KEY = 'auth_database_alias'


class TenantContextMiddleware:
    SESSION_KEY = 'tenant_db_alias'
    CENTRAL_PATHS = (
        '/gestao/', '/admin/', '/auth/login/', '/auth/selecionar-filial/',
        '/auth/filiais/', '/auth/trocar-filial/', '/static/', '/media/',
    )

    def __init__(self, get_response):
        if settings.TENANT_DATABASE_ROUTING_ENABLED:
            missing = [
                name for name in (
                    'TENANT_PUBLIC_LINK_ROUTING_READY',
                    'TENANT_BACKGROUND_TASKS_READY',
                )
                if not getattr(settings, name, False)
            ]
            if missing:
                raise ImproperlyConfigured(
                    'Roteamento multibanco bloqueado até concluir: ' + ', '.join(missing)
                )
        self.get_response = get_response

    def __call__(self, request):
        alias = None
        requested_alias = None
        if (
            settings.TENANT_DATABASE_ROUTING_ENABLED
            and not request.path.startswith(self.CENTRAL_PATHS)
        ):
            requested_alias = request.session.get(self.SESSION_KEY)
            if not requested_alias:
                requested_alias = TenantPublicLinkService.resolve_path(request.path)
            alias = requested_alias
            if requested_alias:
                try:
                    banco = EmpresaBanco.objects.using('default').get(
                        db_alias=requested_alias,
                        ativo=True,
                        status=EmpresaBanco.Status.ATIVO,
                    )
                    if not register_tenant_database(banco):
                        alias = None
                except EmpresaBanco.DoesNotExist:
                    alias = None
                except Exception:
                    logger.exception('Falha ao preparar banco da empresa.')
                    alias = None
            if requested_alias and not alias:
                request.session.pop(self.SESSION_KEY, None)
                request.session.pop('filial_ativa_id', None)
                if 'application/json' in request.headers.get('Accept', ''):
                    return JsonResponse(
                        {'detail': 'Banco da empresa temporariamente indisponível.'},
                        status=503,
                    )
                return HttpResponseRedirect('/auth/login/')

        # O superusuário continua autenticado pelo banco gerencial.
        if request.session.get(AUTH_DATABASE_SESSION_KEY) == 'default':
            request.user.is_authenticated
            if request.user.is_authenticated and request.user.is_superuser:
                request.user.perfil
                request.user.empresa
                if request.user.filial_id:
                    request.user.filial

        request.tenant_db_alias = alias
        token = set_current_tenant_db(alias)
        try:
            return self.get_response(request)
        finally:
            reset_current_tenant_db(token)
            if alias and alias in connections.databases:
                connections[alias].close()
