"""Autenticação e troca de filial com suporte opcional a banco por empresa."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone

from apps.core.middleware.audit import get_client_ip
from apps.core.middleware.tenant import AUTH_DATABASE_SESSION_KEY
from apps.core.models import EmpresaBanco, Filial, LogAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError, PermissaoNegadaError
from apps.core.tenant_context import tenant_db
from apps.core.tenant_registry import register_tenant_database


logger = logging.getLogger(__name__)


class AuthService:
    MAX_TENTATIVAS = 5
    BLOQUEIO_MINUTOS = 15

    @classmethod
    def login(cls, request, email: str, senha: str) -> Usuario:
        email = (email or '').strip().lower()
        tenant_alias = cls._resolver_tenant_por_email(email)
        if tenant_alias:
            request.session['tenant_db_alias'] = tenant_alias
            request.tenant_db_alias = tenant_alias
        else:
            request.session.pop('tenant_db_alias', None)
            request.tenant_db_alias = None

        with tenant_db(tenant_alias):
            user = authenticate(request, username=email, password=senha)
        if user is None:
            cls._registrar_falha(request, email, tenant_alias)
            raise DadosInvalidosError('E-mail ou senha incorretos.')
        if not user.ativo:
            raise DadosInvalidosError('Usuário desativado. Contate o administrador.')
        if user.bloqueado_ate and user.bloqueado_ate > timezone.now():
            minutos = int((user.bloqueado_ate - timezone.now()).total_seconds() / 60)
            raise DadosInvalidosError(
                f'Usuário bloqueado por tentativas inválidas. Tente novamente em {minutos} minuto(s).'
            )

        if user.is_superuser:
            request.session[AUTH_DATABASE_SESSION_KEY] = 'default'
        else:
            request.session.pop(AUTH_DATABASE_SESSION_KEY, None)

        db_alias = tenant_alias or 'default'
        Usuario.objects.using(db_alias).filter(pk=user.pk).update(
            tentativas_login_falhas=0,
            bloqueado_ate=None,
            ultimo_acesso=timezone.now(),
            ip_ultimo_acesso=get_client_ip(request),
        )
        LogAcesso.objects.using(db_alias).create(
            usuario=user,
            filial=user.filial,
            tipo=LogAcesso.Tipo.LOGIN,
            ip_acesso=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            sucesso=True,
        )
        return user

    @staticmethod
    def _resolver_tenant_por_email(email):
        if not settings.TENANT_DATABASE_ROUTING_ENABLED:
            return None
        usuario = (
            Usuario.objects.using('default')
            .select_related('empresa')
            .filter(email__iexact=email, ativo=True, empresa__ativo=True)
            .first()
        )
        if not usuario or usuario.is_superuser:
            return None
        banco = (
            EmpresaBanco.objects.using('default')
            .filter(
                empresa_id=usuario.empresa_id,
                ativo=True,
                status=EmpresaBanco.Status.ATIVO,
            )
            .first()
        )
        if not banco:
            raise DadosInvalidosError('Esta empresa ainda não possui banco ativo para acesso.')
        if not register_tenant_database(banco):
            raise DadosInvalidosError('Banco da empresa temporariamente indisponível.')
        return banco.db_alias

    @classmethod
    def _registrar_falha(cls, request, email, tenant_alias=None):
        db_alias = tenant_alias or 'default'
        try:
            user = Usuario.objects.using(db_alias).get(email=email)
        except Usuario.DoesNotExist:
            return
        tentativas = user.tentativas_login_falhas + 1
        bloqueado_ate = None
        if tentativas >= cls.MAX_TENTATIVAS:
            bloqueado_ate = timezone.now() + timezone.timedelta(minutes=cls.BLOQUEIO_MINUTOS)
            tentativas = 0
        Usuario.objects.using(db_alias).filter(pk=user.pk).update(
            tentativas_login_falhas=tentativas, bloqueado_ate=bloqueado_ate,
        )
        LogAcesso.objects.using(db_alias).create(
            usuario=user,
            filial=user.filial,
            tipo=LogAcesso.Tipo.BLOQUEIO if bloqueado_ate else LogAcesso.Tipo.SENHA_ERRADA,
            ip_acesso=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            sucesso=False,
        )

    @staticmethod
    def trocar_filial(request, filial_id: int) -> Filial:
        manager = Filial.objects.using('default') if settings.TENANT_DATABASE_ROUTING_ENABLED else Filial.objects
        try:
            filial = manager.get(pk=filial_id, ativo=True, empresa__ativo=True)
        except Filial.DoesNotExist:
            raise DadosInvalidosError('Filial não encontrada.')
        if not request.user.pode_acessar_filial(filial):
            raise PermissaoNegadaError('Você não tem acesso a essa filial.')

        if settings.TENANT_DATABASE_ROUTING_ENABLED:
            banco = (
                EmpresaBanco.objects.using('default')
                .filter(
                    empresa_id=filial.empresa_id,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                )
                .first()
            )
            if not banco or not register_tenant_database(banco):
                raise DadosInvalidosError('Esta empresa não possui banco ativo para acesso.')
            request.session['tenant_db_alias'] = banco.db_alias
            request.tenant_db_alias = banco.db_alias
        if request.user.is_superuser:
            request.session[AUTH_DATABASE_SESSION_KEY] = 'default'
        request.session['filial_ativa_id'] = filial.pk
        return filial

    @staticmethod
    def logout_registro(request):
        if not request.user.is_authenticated:
            return
        db_alias = request.session.get(AUTH_DATABASE_SESSION_KEY) or request.user._state.db or 'default'
        filial = getattr(request, 'filial_ativa', None)
        filial_id = filial.pk if filial is not None and filial._state.db == db_alias else None
        try:
            LogAcesso.objects.using(db_alias).create(
                usuario_id=request.user.pk,
                filial_id=filial_id,
                tipo=LogAcesso.Tipo.LOGOUT,
                ip_acesso=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                sucesso=True,
            )
        except Exception:
            logger.exception('Falha ao registrar logout; a sessão será encerrada.')
