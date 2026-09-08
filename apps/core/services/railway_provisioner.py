"""Provisionamento idempotente de um PostgreSQL dedicado por empresa."""
import os
import secrets
import time
from urllib.parse import quote

import environ
import requests
from django.conf import settings
from django.db import connections
from django.utils.text import slugify

from apps.core.models import RailwayProjectPool
from apps.core.services.railway_pool_service import RailwayPoolService


class RailwayProvisioningError(Exception):
    pass


class RailwayApiClient:
    API_URL = 'https://backboard.railway.com/graphql/v2'

    def __init__(self, project_id, environment_id, *, project_token='', api_token=''):
        self.project_id = project_id
        self.environment_id = environment_id
        self.project_token = project_token
        self.api_token = api_token

    @property
    def headers(self):
        if self.project_token:
            return {'Project-Access-Token': self.project_token}
        if self.api_token:
            return {'Authorization': f'Bearer {self.api_token}'}
        raise RailwayProvisioningError('Token Railway não configurado para o projeto.')

    def graphql(self, query, variables=None):
        try:
            response = requests.post(
                self.API_URL,
                json={'query': query, 'variables': variables or {}},
                headers=self.headers,
                timeout=45,
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RailwayProvisioningError(f'Falha ao consultar o Railway: {exc}') from exc
        if response.status_code >= 400 or payload.get('errors'):
            messages = '; '.join(
                error.get('message', 'Erro desconhecido')
                for error in payload.get('errors', [])
            )
            raise RailwayProvisioningError(messages or f'HTTP {response.status_code}')
        return payload['data']

    def services(self):
        query = '''query($id: String!) { project(id: $id) {
          services { edges { node { id name } } }
        } }'''
        edges = self.graphql(query, {'id': self.project_id})['project']['services']['edges']
        return [edge['node'] for edge in edges]

    def find_service(self, name, service_id=''):
        services = self.services()
        if service_id:
            existing = next((item for item in services if item['id'] == service_id), None)
            if existing:
                return existing
        return next((item for item in services if item['name'] == name), None)

    def create_service(self, name, password):
        query = '''mutation($input: ServiceCreateInput!) {
          serviceCreate(input: $input) { id name }
        }'''
        variables = {'input': {
            'projectId': self.project_id,
            'environmentId': self.environment_id,
            'name': name,
            'source': {'image': settings.RAILWAY_TENANT_DATABASE_IMAGE},
            'variables': {
                'POSTGRES_DB': 'railway',
                'POSTGRES_USER': 'postgres',
                'POSTGRES_PASSWORD': password,
                'PGDATA': f'{settings.RAILWAY_TENANT_DATABASE_VOLUME_PATH}/pgdata',
            },
        }}
        return self.graphql(query, variables)['serviceCreate']

    def volume_instances(self):
        query = '''query($id: String!, $projectId: String!) {
          environment(id: $id, projectId: $projectId) {
            volumeInstances { edges { node { serviceId volume { id name } } } }
          }
        }'''
        edges = self.graphql(query, {
            'id': self.environment_id,
            'projectId': self.project_id,
        })['environment']['volumeInstances']['edges']
        return [edge['node'] for edge in edges]

    def find_volume(self, service_id):
        instance = next(
            (item for item in self.volume_instances() if item['serviceId'] == service_id),
            None,
        )
        return instance['volume'] if instance else None

    def volume_count(self):
        return len(self.volume_instances())

    def create_volume(self, service_id):
        query = '''mutation($input: VolumeCreateInput!) {
          volumeCreate(input: $input) { id name }
        }'''
        return self.graphql(query, {'input': {
            'projectId': self.project_id,
            'environmentId': self.environment_id,
            'serviceId': service_id,
            'mountPath': settings.RAILWAY_TENANT_DATABASE_VOLUME_PATH,
        }})['volumeCreate']

    def tcp_proxies(self, service_id):
        query = '''query($environmentId: String!, $serviceId: String!) {
          tcpProxies(environmentId: $environmentId, serviceId: $serviceId) {
            id domain proxyPort applicationPort
          }
        }'''
        return self.graphql(query, {
            'environmentId': self.environment_id,
            'serviceId': service_id,
        })['tcpProxies']

    def ensure_tcp_proxy(self, service_id):
        existing = next(
            (item for item in self.tcp_proxies(service_id) if item['applicationPort'] == 5432),
            None,
        )
        if existing:
            return existing
        # Este endpoint é atômico e evita confirmar alterações staged de outra
        # pessoa. O contrato está isolado para troca quando a API o remover.
        query = '''mutation($input: TCPProxyCreateInput!) {
          tcpProxyCreate(input: $input) {
            id domain proxyPort applicationPort
          }
        }'''
        return self.graphql(query, {'input': {
            'environmentId': self.environment_id,
            'serviceId': service_id,
            'applicationPort': 5432,
        }})['tcpProxyCreate']

    def deploy_service(self, service_id):
        query = '''mutation($serviceId: String!, $environmentId: String!) {
          serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId)
        }'''
        return self.graphql(query, {
            'serviceId': service_id,
            'environmentId': self.environment_id,
        })['serviceInstanceDeployV2']

    def service_variables(self, service_id):
        query = '''query($projectId: String!, $environmentId: String!, $serviceId: String!) {
          variables(
            projectId: $projectId, environmentId: $environmentId,
            serviceId: $serviceId
          )
        }'''
        return self.graphql(query, {
            'projectId': self.project_id,
            'environmentId': self.environment_id,
            'serviceId': service_id,
        })['variables']

    def set_variable(self, service_id, name, value, *, skip_deploys=True):
        query = '''mutation($input: VariableUpsertInput!) {
          variableUpsert(input: $input)
        }'''
        return self.graphql(query, {'input': {
            'projectId': self.project_id,
            'environmentId': self.environment_id,
            'serviceId': service_id,
            'name': name,
            'value': value,
            'skipDeploys': skip_deploys,
        }})['variableUpsert']

    def delete_variable(self, service_id, name):
        query = '''mutation($input: VariableDeleteInput!) {
          variableDelete(input: $input)
        }'''
        return self.graphql(query, {'input': {
            'projectId': self.project_id,
            'environmentId': self.environment_id,
            'serviceId': service_id,
            'name': name,
        }})['variableDelete']

    def delete_service(self, service_id):
        query = '''mutation($id: String!, $environmentId: String) {
          serviceDelete(id: $id, environmentId: $environmentId)
        }'''
        return self.graphql(query, {
            'id': service_id, 'environmentId': self.environment_id,
        })['serviceDelete']

    def delete_volume(self, volume_id):
        query = '''mutation($volumeId: String!) {
          volumeDelete(volumeId: $volumeId)
        }'''
        return self.graphql(query, {'volumeId': volume_id})['volumeDelete']


class RailwayProvisioner:
    SERVICE_NAME_MAX_LENGTH = 32

    @classmethod
    def provision_postgres(cls, banco):
        cls._validate_settings()
        pool = RailwayPoolService.select_for_banco(banco)
        if pool is None:
            return cls._provision_legacy(banco)
        client = cls._client_for_pool(pool)
        service_name = cls._service_name_for_banco(banco)
        password = secrets.token_urlsafe(30)
        service = client.find_service(
            service_name,
            service_id=banco.railway_database_service_id,
        )
        created = False
        if not service:
            service = client.create_service(service_name, password)
            created = True
        else:
            service_name = service['name']
        service_id = service['id']

        volume = client.find_volume(service_id)
        volume_created = volume is None
        if volume_created:
            volume = client.create_volume(service_id)
        is_public = bool(
            pool and pool.connection_mode == RailwayProjectPool.ConnectionMode.PUBLIC
        )
        proxy = client.ensure_tcp_proxy(service_id) if is_public else None
        if created or volume_created or (is_public and not banco.railway_tcp_proxy_id):
            client.deploy_service(service_id)

        if is_public:
            credentials = {
                'POSTGRES_USER': 'postgres',
                'POSTGRES_PASSWORD': password,
                'POSTGRES_DB': 'railway',
            } if created else client.service_variables(service_id)
            runtime_url = cls._public_database_url(proxy, credentials)
            variable_value = runtime_url
        else:
            runtime_url = (
                f'postgresql://postgres:{password}@{service_name}.railway.internal:5432/railway'
                if created else ''
            )
            variable_value = (
                f'postgresql://${{{{{service_name}.POSTGRES_USER}}}}:'
                f'${{{{{service_name}.POSTGRES_PASSWORD}}}}@'
                f'${{{{{service_name}.RAILWAY_PRIVATE_DOMAIN}}}}:5432/'
                f'${{{{{service_name}.POSTGRES_DB}}}}'
            )

        cls._set_app_variable(banco.database_url_env_var, variable_value)
        banco.railway_database_service_id = service_id
        banco.railway_database_service_name = service_name
        banco.railway_volume_id = volume['id']
        if proxy:
            banco.railway_tcp_proxy_id = proxy['id']
            banco.railway_tcp_proxy_domain = proxy['domain']
            banco.railway_tcp_proxy_port = proxy['proxyPort']
        if pool:
            RailwayPoolService.update_observation(pool, client.volume_count())
        return {
            'service_id': service_id,
            'service_name': service_name,
            'database_url': runtime_url,
            'pool_id': pool.pk if pool else None,
            'connection_mode': pool.connection_mode if pool else 'private',
        }

    @classmethod
    def delete_postgres(cls, banco):
        """Remove banco/volume no projeto correto após o backup obrigatório."""
        cls._validate_settings()
        pool = banco.railway_project_pool
        client = cls._client_for_pool(pool)
        service = client.find_service(
            banco.railway_database_service_name,
            service_id=banco.railway_database_service_id,
        ) if (banco.railway_database_service_id or banco.railway_database_service_name) else None
        service_id = service['id'] if service else banco.railway_database_service_id
        volume_id = banco.railway_volume_id
        variable_error = ''
        cleanup_error = ''
        if banco.database_url_env_var:
            try:
                cls._control_client().delete_variable(
                    cls._control_service_id(), banco.database_url_env_var,
                )
            except RailwayProvisioningError as exc:
                variable_error = str(exc)
        if service_id:
            client.delete_service(service_id)
        if volume_id:
            try:
                client.delete_volume(volume_id)
            except RailwayProvisioningError as exc:
                cleanup_error = str(exc)
        if pool:
            try:
                RailwayPoolService.update_observation(pool, client.volume_count())
            except RailwayProvisioningError:
                pass
        return {
            'service_id': service_id or '',
            'volume_id': volume_id or '',
            'env_var': banco.database_url_env_var,
            'variable_error': variable_error,
            'cleanup_error': cleanup_error,
        }

    @classmethod
    def _provision_legacy(cls, banco):
        """Fluxo original, mantido intacto enquanto a feature flag está desligada."""
        service_name = cls._service_name_for_banco(banco)
        password = secrets.token_urlsafe(30)
        service = cls._find_service(
            service_name,
            service_id=banco.railway_database_service_id,
        )
        created = False
        if not service:
            service = cls._create_service(service_name, password)
            created = True
        else:
            service_name = service['name']
        service_id = service['id']
        volume = cls._find_volume(service_id)
        volume_created = volume is None
        if volume_created:
            volume = cls._create_volume(service_id)
        if created or volume_created:
            cls._deploy_service(service_id)

        private_url = (
            f'postgresql://postgres:{password}@{service_name}.railway.internal:5432/railway'
        )
        reference_url = (
            f'postgresql://${{{{{service_name}.POSTGRES_USER}}}}:'
            f'${{{{{service_name}.POSTGRES_PASSWORD}}}}@'
            f'${{{{{service_name}.RAILWAY_PRIVATE_DOMAIN}}}}:5432/'
            f'${{{{{service_name}.POSTGRES_DB}}}}'
        )
        cls._set_app_variable(banco.database_url_env_var, reference_url)
        banco.railway_database_service_id = service_id
        banco.railway_database_service_name = service_name
        banco.railway_volume_id = volume['id']
        return {
            'service_id': service_id,
            'service_name': service_name,
            'database_url': private_url if created else '',
            'pool_id': None,
            'connection_mode': 'private',
        }

    @classmethod
    def wait_for_database(cls, alias, database_url, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            connections.databases[alias] = {
                **connections.databases['default'],
                **environ.Env.db_url_config(database_url),
                'ATOMIC_REQUESTS': False,
                'AUTOCOMMIT': True,
                'CONN_HEALTH_CHECKS': False,
                'CONN_MAX_AGE': 0,
                'OPTIONS': {'sslmode': 'require'} if 'sslmode=require' in database_url else {},
                'TIME_ZONE': None,
            }
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SELECT 1')
                return True
            except Exception:
                connections[alias].close()
                time.sleep(3)
        return False

    @classmethod
    def _validate_settings(cls):
        if not (settings.RAILWAY_PROJECT_TOKEN or settings.RAILWAY_API_TOKEN):
            raise RailwayProvisioningError('Token do projeto principal não configurado.')
        for name in ('RAILWAY_PROJECT_ID', 'RAILWAY_ENVIRONMENT_ID'):
            if not getattr(settings, name):
                raise RailwayProvisioningError(f'{name} não está configurado.')
        if not cls._control_service_id():
            raise RailwayProvisioningError('RAILWAY_CONTROL_SERVICE_ID não está configurado.')

    @staticmethod
    def _control_service_id():
        return (
            getattr(settings, 'RAILWAY_CONTROL_SERVICE_ID', '')
            or getattr(settings, 'RAILWAY_SERVICE_ID', '')
        )

    @classmethod
    def _client_for_pool(cls, pool):
        if not pool:
            return RailwayApiClient(
                settings.RAILWAY_PROJECT_ID,
                settings.RAILWAY_ENVIRONMENT_ID,
                project_token=settings.RAILWAY_PROJECT_TOKEN,
                api_token=settings.RAILWAY_API_TOKEN,
            )
        return RailwayApiClient(
            pool.railway_project_id,
            pool.railway_environment_id,
            project_token=RailwayPoolService.token_for(pool),
        )

    @classmethod
    def _control_client(cls):
        return RailwayApiClient(
            settings.RAILWAY_PROJECT_ID,
            settings.RAILWAY_ENVIRONMENT_ID,
            project_token=settings.RAILWAY_CONTROL_PROJECT_TOKEN,
            api_token=settings.RAILWAY_API_TOKEN,
        )

    @classmethod
    def _set_app_variable(cls, name, value):
        return cls._control_client().set_variable(
            cls._control_service_id(), name, value, skip_deploys=True,
        )

    @classmethod
    def sync_tenant_variables_from_control_app(cls):
        """Carrega no worker URLs de tenants criados depois do último deploy."""
        cls._validate_settings()
        variables = cls._control_client().service_variables(
            cls._control_service_id(),
        )
        synced = []
        for name, value in variables.items():
            if name.startswith('TENANT_DATABASE_URL_') and value:
                os.environ[name] = str(value)
                synced.append(name)
        return synced

    @staticmethod
    def _public_database_url(proxy, credentials):
        required = ('POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB')
        missing = [key for key in required if not credentials.get(key)]
        if missing:
            raise RailwayProvisioningError(
                'Credenciais PostgreSQL indisponíveis para retomar o provisionamento.'
            )
        user = quote(str(credentials['POSTGRES_USER']), safe='')
        password = quote(str(credentials['POSTGRES_PASSWORD']), safe='')
        database = quote(str(credentials['POSTGRES_DB']), safe='')
        return (
            f'postgresql://{user}:{password}@{proxy["domain"]}:'
            f'{proxy["proxyPort"]}/{database}?sslmode=require'
        )

    @classmethod
    def _service_name_for_banco(cls, banco):
        empresa = banco.empresa
        name = slugify(
            empresa.nome_fantasia or empresa.razao_social or f'empresa-{empresa.pk}'
        )
        document = ''.join(char for char in (empresa.cnpj or '') if char.isdigit())
        suffix = f'-{document}' if document else ''
        max_base = cls.SERVICE_NAME_MAX_LENGTH - len(suffix)
        return f'{name[:max_base].strip("-")}{suffix}'

    # Compatibilidade de baixo nível para testes e integrações existentes.
    @classmethod
    def _graphql(cls, query, variables=None):
        return cls._client_for_pool(None).graphql(query, variables)

    @classmethod
    def _find_service(cls, name, service_id=''):
        query = '''query($id: String!) { project(id: $id) {
          services { edges { node { id name } } }
        } }'''
        edges = cls._graphql(query, {'id': settings.RAILWAY_PROJECT_ID})[
            'project'
        ]['services']['edges']
        services = [edge['node'] for edge in edges]
        if service_id:
            existing = next(
                (service for service in services if service['id'] == service_id), None,
            )
            if existing:
                return existing
        return next((service for service in services if service['name'] == name), None)

    @classmethod
    def _create_service(cls, name, password):
        return cls._client_for_pool(None).create_service(name, password)

    @classmethod
    def _find_volume(cls, service_id):
        query = '''query($id: String!, $projectId: String!) {
          environment(id: $id, projectId: $projectId) {
            volumeInstances { edges { node { serviceId volume { id name } } } }
          }
        }'''
        edges = cls._graphql(query, {
            'id': settings.RAILWAY_ENVIRONMENT_ID,
            'projectId': settings.RAILWAY_PROJECT_ID,
        })['environment']['volumeInstances']['edges']
        instance = next(
            (edge['node'] for edge in edges if edge['node']['serviceId'] == service_id),
            None,
        )
        return instance['volume'] if instance else None

    @classmethod
    def _create_volume(cls, service_id):
        return cls._client_for_pool(None).create_volume(service_id)

    @classmethod
    def _deploy_service(cls, service_id):
        return cls._client_for_pool(None).deploy_service(service_id)
