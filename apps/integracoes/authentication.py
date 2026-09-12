from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from apps.core.middleware.audit import get_client_ip
from apps.core.models import EmpresaBanco
from apps.core.tenant_registry import register_tenant_database

from .models import CredencialIntegracao


class BancoEmpresaIndisponivel(exceptions.APIException):
    status_code = 503
    default_detail = 'Banco da empresa temporariamente indisponível.'
    default_code = 'banco_empresa_indisponivel'


class ChaveApiAuthentication(BaseAuthentication):
    keyword = 'ApiKey'

    def authenticate(self, request):
        token = request.headers.get('X-API-Key', '').strip()
        authorization = request.headers.get('Authorization', '').strip()
        if not token and authorization:
            partes = authorization.split(None, 1)
            if len(partes) == 2 and partes[0].lower() == self.keyword.lower():
                token = partes[1].strip()
        if not token:
            return None
        if not token.startswith('ited_') or '.' not in token:
            raise exceptions.AuthenticationFailed('Chave de API inválida.')

        prefixo = token[5:].split('.', 1)[0]
        credencial = (
            CredencialIntegracao.objects.using('default')
            .select_related('empresa')
            .filter(prefixo=prefixo, token_hash=CredencialIntegracao.hash_token(token))
            .first()
        )
        if not credencial or not credencial.ativo or not credencial.empresa.ativo:
            raise exceptions.AuthenticationFailed('Chave de API inválida ou revogada.')
        if credencial.esta_expirada():
            raise exceptions.AuthenticationFailed('Chave de API expirada.')

        ip = get_client_ip(request) or '0.0.0.0'
        if not credencial.permite_ip(ip):
            raise exceptions.AuthenticationFailed('Origem não autorizada para esta chave.')

        alias = 'default'
        if settings.TENANT_DATABASE_ROUTING_ENABLED:
            banco = (
                EmpresaBanco.objects.using('default')
                .filter(
                    empresa_id=credencial.empresa_id,
                    ativo=True,
                    status=EmpresaBanco.Status.ATIVO,
                )
                .first()
            )
            if not banco or not register_tenant_database(banco):
                raise BancoEmpresaIndisponivel()
            alias = banco.db_alias

        request.integracao_alias = alias
        limite = timezone.now() - timedelta(minutes=5)
        if not credencial.ultimo_uso_em or credencial.ultimo_uso_em < limite:
            CredencialIntegracao.objects.using('default').filter(pk=credencial.pk).update(
                ultimo_uso_em=timezone.now(), ultimo_ip=ip,
            )
        return credencial, credencial

    def authenticate_header(self, request):
        return self.keyword
