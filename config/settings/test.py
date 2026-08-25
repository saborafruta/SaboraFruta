"""Configuracao leve para testes locais automatizados."""
import os

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

from .base import *  # noqa

DEBUG = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

LOCAL_APP_LABELS = [
    'analytics',
    'cashback',
    'cadastros',
    'compras',
    'core',
    'crm',
    'estoque',
    'financeiro',
    'fiscal',
    'food_service',
    'lotes',
    'logistica',
    'mapas',
    'moda',
    'pdv',
    'polpa',
    'producao',
    'produtos',
    'qualidade',
    'vendas',
]

MIGRATION_MODULES = {app_label: None for app_label in LOCAL_APP_LABELS}
