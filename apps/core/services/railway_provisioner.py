"""Provisionamento idempotente de Postgres por empresa no Railway."""
import secrets
import time

import environ
import requests
from django.conf import settings
from django.db import connections
from django.utils.text import slugify


class RailwayProvisioningError(Exception):
    pass


class RailwayProvisioner:
    API_URL = 'https://backboard.railway.com/graphql/v2'
    SERVICE_NAME_MAX_LENGTH = 32

    @classmethod
    def provision_postgres(cls, banco):
        cls._validate_settings()
        service_name = cls._service_name_for_banco(banco)
        password = secrets.token_urlsafe(30)
        service = cls._find_service(service_name)
        created = False
        if not service:
            service = cls._create_service(service_name, password)
            created = True
        service_id = service['id']
        volume = cls._find_volume(f'{service_name}-volume')
        if not volume:
            cls._create_volume(service_id)
        if created or not volume:
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
        return {
            'service_id': service_id,
            'service_name': service_name,
            'database_url': private_url if created else '',
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
                'OPTIONS': {},
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
            raise RailwayProvisioningError('Token do projeto Railway não configurado.')
        for name in ('RAILWAY_PROJECT_ID', 'RAILWAY_ENVIRONMENT_ID', 'RAILWAY_SERVICE_ID'):
            if not getattr(settings, name):
                raise RailwayProvisioningError(f'{name} não está configurado.')

    @classmethod
    def _headers(cls):
        if settings.RAILWAY_PROJECT_TOKEN:
            return {'Project-Access-Token': settings.RAILWAY_PROJECT_TOKEN}
        return {'Authorization': f'Bearer {settings.RAILWAY_API_TOKEN}'}

    @classmethod
    def _graphql(cls, query, variables=None):
        try:
            response = requests.post(
                cls.API_URL,
                json={'query': query, 'variables': variables or {}},
                headers=cls._headers(),
                timeout=45,
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RailwayProvisioningError(f'Falha ao consultar o Railway: {exc}') from exc
        if response.status_code >= 400 or payload.get('errors'):
            messages = '; '.join(e.get('message', 'Erro desconhecido') for e in payload.get('errors', []))
            raise RailwayProvisioningError(messages or f'HTTP {response.status_code}')
        return payload['data']

    @classmethod
    def _create_service(cls, name, password):
        query = '''mutation($input: ServiceCreateInput!) {
          serviceCreate(input: $input) { id name }
        }'''
        variables = {'input': {
            'projectId': settings.RAILWAY_PROJECT_ID,
            'environmentId': settings.RAILWAY_ENVIRONMENT_ID,
            'name': name,
            'source': {'image': settings.RAILWAY_TENANT_DATABASE_IMAGE},
            'variables': {
                'POSTGRES_DB': 'railway', 'POSTGRES_USER': 'postgres',
                'POSTGRES_PASSWORD': password,
                'PGDATA': f'{settings.RAILWAY_TENANT_DATABASE_VOLUME_PATH}/pgdata',
            },
        }}
        return cls._graphql(query, variables)['serviceCreate']

    @classmethod
    def _find_service(cls, name):
        query = '''query($id: String!) { project(id: $id) {
          services { edges { node { id name } } }
        } }'''
        edges = cls._graphql(query, {'id': settings.RAILWAY_PROJECT_ID})['project']['services']['edges']
        return next((edge['node'] for edge in edges if edge['node']['name'] == name), None)

    @classmethod
    def _create_volume(cls, service_id):
        query = '''mutation($input: VolumeCreateInput!) { volumeCreate(input: $input) { id } }'''
        return cls._graphql(query, {'input': {
            'projectId': settings.RAILWAY_PROJECT_ID,
            'environmentId': settings.RAILWAY_ENVIRONMENT_ID,
            'serviceId': service_id,
            'mountPath': settings.RAILWAY_TENANT_DATABASE_VOLUME_PATH,
        }})['volumeCreate']

    @classmethod
    def _find_volume(cls, name):
        query = '''query($id: String!) { project(id: $id) {
          volumes { edges { node { id name } } }
        } }'''
        edges = cls._graphql(query, {'id': settings.RAILWAY_PROJECT_ID})['project']['volumes']['edges']
        return next((edge['node'] for edge in edges if edge['node']['name'] == name), None)

    @classmethod
    def _deploy_service(cls, service_id):
        query = '''mutation($serviceId: String!, $environmentId: String!) {
          serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId)
        }'''
        return cls._graphql(query, {
            'serviceId': service_id,
            'environmentId': settings.RAILWAY_ENVIRONMENT_ID,
        })

    @classmethod
    def _set_app_variable(cls, name, value):
        query = '''mutation($input: VariableUpsertInput!) { variableUpsert(input: $input) }'''
        return cls._graphql(query, {'input': {
            'projectId': settings.RAILWAY_PROJECT_ID,
            'environmentId': settings.RAILWAY_ENVIRONMENT_ID,
            'serviceId': settings.RAILWAY_SERVICE_ID,
            'name': name,
            'value': value,
            'skipDeploys': True,
        }})

    @classmethod
    def _service_name_for_banco(cls, banco):
        empresa = banco.empresa
        name = slugify(empresa.nome_fantasia or empresa.razao_social or f'empresa-{empresa.pk}')
        document = ''.join(char for char in (empresa.cnpj or '') if char.isdigit())
        suffix = f'-{document}' if document else ''
        max_base = cls.SERVICE_NAME_MAX_LENGTH - len(suffix)
        return f'{name[:max_base].strip("-")}{suffix}'
