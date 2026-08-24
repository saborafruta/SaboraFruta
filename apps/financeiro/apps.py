from django.apps import AppConfig


class FinanceiroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.financeiro"
    verbose_name = "Financeiro, Fiscal e DRE"

    def ready(self):
        from . import signals  # noqa: F401
