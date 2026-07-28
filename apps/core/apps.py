from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Core'

    def ready(self):
        from . import db_lookups  # noqa: F401
        from . import signals  # noqa
        from .models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
        from .signals import register_for_audit

        register_for_audit(Empresa, 'config')
        register_for_audit(Filial, 'config')
        register_for_audit(Usuario, 'config')
        register_for_audit(PerfilAcesso, 'config')
        register_for_audit(Permissao, 'config')
