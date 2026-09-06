"""Contexto isolado do banco ativo em cada request ou tarefa."""
from contextlib import contextmanager
from contextvars import ContextVar


_current_tenant_db = ContextVar('current_tenant_db', default=None)


def get_current_tenant_db():
    return _current_tenant_db.get()


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
