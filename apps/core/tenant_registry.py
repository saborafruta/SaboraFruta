"""Registro dinâmico e seguro de conexões dos bancos das empresas."""
import os

import environ
from django.conf import settings
from django.db import connections


def tenant_database_url_from_env(banco):
    if not banco.database_url_env_var:
        return ''
    return os.environ.get(banco.database_url_env_var, '')


def register_tenant_database(banco):
    database_url = tenant_database_url_from_env(banco)
    if not database_url:
        return False
    config = environ.Env.db_url_config(database_url)
    normalized = {**connections.databases.get('default', {}), **config}
    normalized.update({
        'ATOMIC_REQUESTS': False,
        'AUTOCOMMIT': True,
        'CONN_HEALTH_CHECKS': False,
        'CONN_MAX_AGE': 0,
        'TIME_ZONE': None,
    })
    options = {**normalized.get('OPTIONS', {})}
    if 'postgresql' in normalized.get('ENGINE', ''):
        options.setdefault('connect_timeout', settings.DATABASE_CONNECT_TIMEOUT)
        server_options = options.get('options', '')
        if 'statement_timeout=' not in server_options:
            flags = (
                f'-c statement_timeout={settings.DATABASE_STATEMENT_TIMEOUT_MS} '
                f'-c lock_timeout={settings.DATABASE_LOCK_TIMEOUT_MS} '
                '-c idle_in_transaction_session_timeout='
                f'{settings.DATABASE_IDLE_TRANSACTION_TIMEOUT_MS}'
            )
            options['options'] = f'{server_options} {flags}'.strip()
    normalized['OPTIONS'] = options
    normalized.setdefault('TEST', {
        'CHARSET': None, 'COLLATION': None, 'MIGRATE': True,
        'MIRROR': None, 'NAME': None,
    })
    connections.databases[banco.db_alias] = normalized
    if banco.db_alias not in settings.TENANT_DATABASE_ALIASES:
        settings.TENANT_DATABASE_ALIASES.append(banco.db_alias)
    return True
