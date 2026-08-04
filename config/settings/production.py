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

DATABASES = {
    'default': {
        **database_config,
        'CONN_MAX_AGE': env.int('DATABASE_CONN_MAX_AGE', default=0),
    }
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
