"""Autenticação e troca de filial com suporte opcional a banco por empresa."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone

from apps.core.middleware.audit import get_client_ip
from apps.core.middleware.tenant import AUTH_DATABASE_SESSION_KEY
from apps.core.models import EmpresaBanco, Filial, LogAcesso, PerfilAcesso, Usuario
from apps.core.services.exceptions import DadosInvalidosError, PermissaoNegadaError
from apps.core.tenant_registry import register_tenant_database
from apps.core.services.tenant_user_service import TenantUserService


logger = logging.getLogger(__name__)


class AuthService:
    MAX_TENTATIVAS = 5
    BLOQUEIO_MINUTOS = 15

    @classmethod
    def login(cls, request, email: str, senha: str) -> Usuario:
        email = (email or '').strip().lower()
        request.session.pop('tenant_db_alias', None)
        request.session.pop('filial_ativa_id', None)
        request.tenant_db_alias = None

        # Identidade, senha e bloqueio pertencem ao diretorio gerencial. O
        # tenant so e escolhido depois, a partir das empresas/filiais que o
        # usuario pode acessar.
        authenticated_user = authenticate(request, username=email, password=senha)
        if authenticated_user is None:
            cls._registrar_falha(request, email)
            raise DadosInvalidosError('E-mail ou senha incorretos.')
        user = authenticated_user
        if not user.ativo:
            raise DadosInvalidosError('Usuário desativado. Contate o administrador.')
        if user.bloqueado_ate and user.bloqueado_ate > timezone.now():
            minutos = int((user.bloqueado_ate - timezone.now()).total_seconds() / 60)
            raise DadosInvalidosError(
                f'Usuário bloqueado por tentativas inválidas. Tente novamente em {minutos} minuto(s).'
            )

        request.session[AUTH_DATABASE_SESSION_KEY] = 'default'

        Usuario.objects.using('default').filter(pk=user.pk).update(
            tentativas_login_falhas=0,
            bloqueado_ate=None,
            ultimo_acesso=timezone.now(),
            ip_ultimo_acesso=get_client_ip(request),
        )
        LogAcesso.objects.using('default').create(
            usuario_id=user.pk,
            filial_id=user.filial_id,
            tipo=LogAcesso.Tipo.LOGIN,
            ip_acesso=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            sucesso=True,
        )
        return user

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
            usuario_id=user.pk,
            filial_id=user.filial_id,
            tipo=LogAcesso.Tipo.BLOQUEIO if bloqueado_ate else LogAcesso.Tipo.SENHA_ERRADA,
            ip_acesso=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            sucesso=False,
        )

    @staticmethod
    def trocar_filial(request, filial_id: int) -> Filial:
        tenant_alias = getattr(request, 'tenant_db_alias', None)
        is_central_identity = (
            request.session.get(AUTH_DATABASE_SESSION_KEY) == 'default'
            and request.user._state.db == 'default'
        )
        lookup_alias = 'default' if is_central_identity else (tenant_alias or 'default')
        manager = Filial.objects.using(lookup_alias)
        try:
            filial = manager.get(pk=filial_id, ativo=True, empresa__ativo=True)
        except Filial.DoesNotExist:
            raise DadosInvalidosError('Filial não encontrada.')
        if not request.user.pode_acessar_filial(filial):
            raise PermissaoNegadaError('Você não tem acesso a essa filial.')

        if settings.TENANT_DATABASE_ROUTING_ENABLED:
            if tenant_alias and not is_central_identity:
                banco = EmpresaBanco.objects.using('default').filter(
                    db_alias=tenant_alias,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                ).first()
            else:
                banco = EmpresaBanco.objects.using('default').filter(
                    empresa_id=filial.empresa_id,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                ).first()
            if not banco or not register_tenant_database(banco):
                raise DadosInvalidosError('Esta empresa não possui banco ativo para acesso.')
            if is_central_identity:
                try:
                    filial = Filial.objects.using(banco.db_alias).get(cnpj=filial.cnpj)
                except Filial.DoesNotExist as exc:
                    raise DadosInvalidosError(
                        'A filial ainda não foi localizada no banco da empresa.'
                    ) from exc
                if not request.user.is_superuser:
                    try:
                        TenantUserService.resolver_usuario(
                            alias=banco.db_alias,
                            usuario_central=request.user,
                            filial_id=filial.pk,
                        )
                    except (
                        Filial.DoesNotExist,
                        PerfilAcesso.DoesNotExist,
                        Usuario.DoesNotExist,
                    ) as exc:
                        raise DadosInvalidosError(
                            'Nao foi possivel preparar o acesso operacional deste usuario.'
                        ) from exc
            # A sessao so pode apontar para o novo tenant depois de confirmar
            # que a filial existe nele. Se a copia do diretorio estiver
            # incompleta, manter metade da troca fazia o dashboard abrir com o
            # banco novo e a filial antiga (que pode ter o mesmo PK em outro
            # banco), parecendo entrar em uma empresa diferente.
            request.session['tenant_db_alias'] = banco.db_alias
            request.tenant_db_alias = banco.db_alias
        if is_central_identity:
            request.session[AUTH_DATABASE_SESSION_KEY] = 'default'
        request.session['filial_ativa_id'] = filial.pk
        return filial

    @staticmethod
    def logout_registro(request):
        if not request.user.is_authenticated:
            return
        db_alias = request.session.get(AUTH_DATABASE_SESSION_KEY) or request.user._state.db or 'default'
        usuario = (
            getattr(request, '_central_authenticated_user', None)
            if db_alias == 'default'
            else None
        ) or request.user
        filial = getattr(request, 'filial_ativa', None)
        filial_id = filial.pk if filial is not None and filial._state.db == db_alias else None
        try:
            LogAcesso.objects.using(db_alias).create(
                usuario_id=usuario.pk,
                filial_id=filial_id,
                tipo=LogAcesso.Tipo.LOGOUT,
                ip_acesso=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                sucesso=True,
            )
        except Exception:
            logger.exception('Falha ao registrar logout; a sessão será encerrada.')
