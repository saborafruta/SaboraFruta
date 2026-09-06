"""
Configurações de produção — Railway
"""
from .base import *  # noqa

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['.railway.app', '*'])

# Banco de producao.
# MVP atual: Railway interno pode continuar sem sslmode explicito.
# Futuro Supabase/Postgres externo: configurar DATABASE_SSL_REQUIRE=True ou
# incluir ?sslmode=require na DATABASE_URL.
database_config = env.db('DATABASE_URL')
if env.bool('DATABASE_SSL_REQUIRE', default=False):
    database_config.setdefault('OPTIONS', {})
    database_config['OPTIONS']['sslmode'] = 'require'

def _with_database_timeouts(config):
    config = {**config}
    if 'postgresql' not in config.get('ENGINE', ''):
        return config
    options = {**config.get('OPTIONS', {})}
    options.setdefault('connect_timeout', DATABASE_CONNECT_TIMEOUT)
    server_options = options.get('options', '')
    timeout_flags = (
        f'-c statement_timeout={DATABASE_STATEMENT_TIMEOUT_MS} '
        f'-c lock_timeout={DATABASE_LOCK_TIMEOUT_MS} '
        f'-c idle_in_transaction_session_timeout={DATABASE_IDLE_TRANSACTION_TIMEOUT_MS}'
    )
    options['options'] = f'{server_options} {timeout_flags}'.strip()
    config['OPTIONS'] = options
    return config


tenant_database_configs = {
    alias: DATABASES[alias]
    for alias in TENANT_DATABASE_ALIASES
    if alias in DATABASES
}

DATABASES = {
    'default': {
        **_with_database_timeouts(database_config),
        'CONN_MAX_AGE': env.int('DATABASE_CONN_MAX_AGE', default=0),
    }
}
for alias, config in tenant_database_configs.items():
    DATABASES[alias] = {
        **_with_database_timeouts(config),
        'CONN_MAX_AGE': config.get(
            'CONN_MAX_AGE', env.int('DATABASE_CONN_MAX_AGE', default=0),
        ),
    }

# Whitenoise — serve arquivos estáticos direto pelo Django, sem precisar de nginx
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
if 'STORAGES' in globals():
    STORAGES['staticfiles'] = {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    }

# Uploads no Railway
# O volume persistente deve ser montado em /app/media. Se MEDIA_ROOT nao for
# informado no painel, este default evita salvar dentro da pasta do projeto.
MEDIA_ROOT = Path(env('MEDIA_ROOT', default='/app/media'))
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
MEDIA_URL = env('MEDIA_URL', default='/media/')
if not MEDIA_URL.endswith('/'):
    MEDIA_URL = f'{MEDIA_URL}/'

# Segurança básica para HTTPS
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Em producao, atualiza o NCM sob demanda caso a carga noturna ainda nao tenha
# sido executada ou uma nova vigencia tenha comecado.
IBPT_AUTO_SYNC = env.bool('IBPT_AUTO_SYNC', default=True)
IBPT_INTERNAL_SCHEDULER = env.bool('IBPT_INTERNAL_SCHEDULER', default=True)
