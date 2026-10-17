from django.conf import settings
from django.db import connections
from rest_framework import exceptions, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.models import EmpresaBanco, Filial, PerfilAcesso, Usuario
from apps.core.services.tenant_user_service import TenantUserService
from apps.core.tenant_context import reset_current_tenant_db, set_current_tenant_db
from apps.core.tenant_registry import register_tenant_database


class BancoTenantIndisponivel(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = 'O banco da empresa está temporariamente indisponível.'
    default_code = 'tenant_indisponivel'


class MobileTenantAPIView(APIView):
    """Prepara tenant, filial e identidade operacional depois do JWT central."""

    permission_classes = [IsAuthenticated]
    permissao_modulo = None
    permissao_acao = 'ver'

    def initial(self, request, *args, **kwargs):
        # A rota /api/v1/ é central. O JWT é autenticado no Banco Gerencial
        # antes de ativarmos o router do banco operacional.
        super().initial(request, *args, **kwargs)
        usuario_central = request.user

        filial_id = request.headers.get('X-Filial-ID', '').strip()
        if not filial_id.isdigit():
            raise exceptions.ValidationError({
                'filial': 'Informe uma filial válida no cabeçalho X-Filial-ID.',
            })
        filial_central = (
            Filial.objects.using('default')
            .select_related('empresa')
            .filter(pk=int(filial_id), ativo=True, empresa__ativo=True)
            .first()
        )
        if filial_central is None or not usuario_central.pode_acessar_filial(filial_central):
            raise exceptions.PermissionDenied('Você não possui acesso à filial informada.')

        alias = 'default'
        filial_operacional = filial_central
        usuario_operacional = usuario_central
        if settings.TENANT_DATABASE_ROUTING_ENABLED:
            alias = request.headers.get('X-Tenant-Alias', '').strip()
            banco = (
                EmpresaBanco.objects.using('default')
                .filter(
                    db_alias=alias,
                    empresa_id=filial_central.empresa_id,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                )
                .first()
            )
            if banco is None or not register_tenant_database(banco):
                raise BancoTenantIndisponivel()

            self._tenant_alias = alias
            self._tenant_token = set_current_tenant_db(alias)
            filial_operacional = (
                Filial.objects.using(alias)
                .select_related('empresa')
                .filter(cnpj=filial_central.cnpj, ativo=True, empresa__ativo=True)
                .first()
            )
            if filial_operacional is None:
                raise BancoTenantIndisponivel(
                    'A filial não foi localizada no banco operacional da empresa.'
                )
            try:
                if usuario_central.is_superuser:
                    usuario_operacional = TenantUserService.resolver_superusuario(
                        alias=alias,
                        usuario_central=usuario_central,
                        filial_id=filial_operacional.pk,
                    )
                else:
                    usuario_operacional = TenantUserService.resolver_usuario(
                        alias=alias,
                        usuario_central=usuario_central,
                        filial_id=filial_operacional.pk,
                    )
            except (Filial.DoesNotExist, PerfilAcesso.DoesNotExist, Usuario.DoesNotExist) as exc:
                # PermissionDenied mantém detalhes internos de banco fora da API.
                raise exceptions.PermissionDenied(
                    'Não foi possível validar seu acesso à filial selecionada.'
                ) from exc

        usuario_operacional._perfil_ativo = usuario_operacional.perfil_para_filial(
            filial_operacional,
        )
        request._central_authenticated_user = usuario_central
        request.user = usuario_operacional
        request.filial_ativa = filial_operacional
        request.tenant_db_alias = alias

        if self.permissao_modulo and not request.user.tem_permissao(
            self.permissao_modulo,
            self.permissao_acao,
        ):
            raise exceptions.PermissionDenied('Você não tem permissão para esta operação.')

    def finalize_response(self, request, response, *args, **kwargs):
        try:
            return super().finalize_response(request, response, *args, **kwargs)
        finally:
            token = getattr(self, '_tenant_token', None)
            if token is not None:
                reset_current_tenant_db(token)
            alias = getattr(self, '_tenant_alias', None)
            if alias and alias in connections.databases:
                connections[alias].close()
