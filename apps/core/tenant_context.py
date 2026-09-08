"""Contexto isolado do banco ativo em cada request ou tarefa."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from django.db import DEFAULT_DB_ALIAS, transaction


_current_tenant_db = ContextVar('current_tenant_db', default=None)


def get_current_tenant_db():
    return _current_tenant_db.get()


def get_current_database_alias():
    """Retorna a conexão operacional ativa ou o banco padrão."""
    return get_current_tenant_db() or DEFAULT_DB_ALIAS


def set_current_tenant_db(alias):
    return _current_tenant_db.set(alias)


def reset_current_tenant_db(token):
    _current_tenant_db.reset(token)


@contextmanager
def tenant_db(alias):
    token = set_current_tenant_db(alias)
    try:
        yield
    finally:
        reset_current_tenant_db(token)


def tenant_atomic(func=None):
    """Abre a transação na mesma conexão escolhida pelo router do tenant."""
    if func is None:
        return transaction.atomic(using=get_current_database_alias())

    @wraps(func)
    def wrapped(*args, **kwargs):
        with transaction.atomic(using=get_current_database_alias()):
            return func(*args, **kwargs)

    return wrapped
