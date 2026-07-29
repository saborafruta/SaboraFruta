"""
ConfiguraÃ§Ãµes base do projeto ERP iNoovaTed.
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(DEBUG=(bool, False))
env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env('SECRET_KEY', default='change-me')
DEBUG = env('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Apps
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'widget_tweaks',
    'django_celery_beat',
    'django_celery_results',
]

LOCAL_APPS = [
    'apps.core',
    'apps.cadastros',
    'apps.produtos',
    'apps.estoque',
    'apps.producao',
    'apps.vendas',
    'apps.compras',
    # Novos mÃ³dulos (mai/2026)
    'apps.financeiro',
    'apps.fiscal',
    'apps.logistica',
    'apps.pdv',
    'apps.qualidade',
    'apps.analytics',
    'apps.lotes',
    'apps.cashback',
    'apps.crm',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Middlewares customizados
    'apps.core.middleware.filial.FilialMiddleware',
    'apps.core.middleware.audit.AuditMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.core.context_processors.filial_context',
                'apps.core.context_processors.parametros_sistema',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': env.db("DATABASE_URL"),
}

# Auth
AUTH_USER_MODEL = 'core.Usuario'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/auth/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/auth/login/'

# i18n
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Static / Media
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = env('MEDIA_URL', default='/media/')
MEDIA_ROOT = Path(env('MEDIA_ROOT', default=str(BASE_DIR / 'media')))

# PK padrÃ£o: INTEGER incremental (requisito do cliente)
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework + JWT
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# Cache â€” Django nativo (LocMem em dev)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'erp-inoovated-cache',
    }
}

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='django-db')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Transparencia tributaria (Lei 12.741/2012 / tabela IBPT).
IBPT_API_BASE_URL = env(
    'IBPT_API_BASE_URL',
    default='https://api-ibpt.seunegocionanuvem.com.br',
)
IBPT_AUTO_SYNC = env.bool('IBPT_AUTO_SYNC', default=False)
IBPT_INTERNAL_SCHEDULER = env.bool('IBPT_INTERNAL_SCHEDULER', default=False)

# Email
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@inoovated.com.br')

# IntegraÃ§Ãµes externas
VIACEP_URL = env('VIACEP_URL', default='https://viacep.com.br/ws/{cep}/json/')
FISCAL_DFE_MODE = env('FISCAL_DFE_MODE', default='local')
FISCAL_DFE_ENABLE_REAL_CONSULTA = env.bool('FISCAL_DFE_ENABLE_REAL_CONSULTA', default=False)
FISCAL_DFE_ENABLE_REAL_EVENTS = env.bool('FISCAL_DFE_ENABLE_REAL_EVENTS', default=False)
FISCAL_DFE_CERT_PASSWORD = env('FISCAL_DFE_CERT_PASSWORD', default='')
FISCAL_DFE_DIST_VERSION = env('FISCAL_DFE_DIST_VERSION', default='1.01')
FISCAL_DFE_SEFAZ_TIMEOUT = env.int('FISCAL_DFE_SEFAZ_TIMEOUT', default=30)
FISCAL_DFE_EMPTY_COOLDOWN_MINUTES = env.int('FISCAL_DFE_EMPTY_COOLDOWN_MINUTES', default=60)
FISCAL_DFE_SEFAZ_ENDPOINT_HOMOLOGACAO = env(
    'FISCAL_DFE_SEFAZ_ENDPOINT_HOMOLOGACAO',
    default='https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
)
FISCAL_DFE_SEFAZ_ENDPOINT_PRODUCAO = env(
    'FISCAL_DFE_SEFAZ_ENDPOINT_PRODUCAO',
    default='https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx',
)
FISCAL_ALLOW_PRODUCTION_ENVIRONMENT = env.bool('FISCAL_ALLOW_PRODUCTION_ENVIRONMENT', default=False)
FISCAL_ALLOW_PRODUCTION_EMISSION = env.bool('FISCAL_ALLOW_PRODUCTION_EMISSION', default=False)

# Focus NFe â€” emissÃ£o de documentos fiscais (https://doc.focusnfe.com.br)
ERP_FOCUSNFE_TOKEN = env('FOCUSNFE_TOKEN', default='')
ERP_FOCUSNFE_AMBIENTE = env.int('FOCUSNFE_AMBIENTE', default=2)   # 1=producao, 2=homologacao
ERP_FOCUSNFE_WEBHOOK_TOKEN = env('FOCUSNFE_WEBHOOK_TOKEN', default='')

# SessÃ£o
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 horas


# Logging — sem isso, uma excecao 500 nao tratada em producao (DEBUG=False)
# simplesmente some: nao aparece no console/stdout, entao os logs do Railway
# nao mostram nada util pra investigar. O handler abaixo garante que todo
# traceback vai para stdout (capturado como log do deployment).
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
