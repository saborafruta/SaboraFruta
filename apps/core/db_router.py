"""Roteamento de modelos operacionais para o banco da empresa ativa."""
from django.conf import settings

from apps.core.tenant_context import get_current_tenant_db


class TenantDatabaseRouter:
    def _tenant_alias_for_model(self, model):
        if not getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False):
            return None
        if model._meta.label_lower in getattr(settings, 'TENANT_GLOBAL_MODELS', []):
            return 'default'
        if model._meta.app_label not in getattr(settings, 'TENANT_ROUTED_APPS', []):
            return None
        alias = get_current_tenant_db()
        if alias in getattr(settings, 'TENANT_DATABASE_ALIASES', []):
            return alias
        return None

    def db_for_read(self, model, **hints):
        return self._tenant_alias_for_model(model)

    def db_for_write(self, model, **hints):
        return self._tenant_alias_for_model(model)

    def allow_relation(self, obj1, obj2, **hints):
        databases = ['default', *getattr(settings, 'TENANT_DATABASE_ALIASES', [])]
        if obj1._state.db in databases and obj2._state.db in databases:
            return obj1._state.db == obj2._state.db
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        label = f'{app_label}.{model_name}' if model_name else ''
        if label in getattr(settings, 'TENANT_GLOBAL_MODELS', []):
            return db == 'default'
        if db == 'default':
            return None
        if db in getattr(settings, 'TENANT_DATABASE_ALIASES', []):
            return app_label in getattr(settings, 'TENANT_ROUTED_APPS', [])
        return None
