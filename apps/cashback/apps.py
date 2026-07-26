from django.apps import AppConfig


class CashbackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cashback"
    verbose_name = "Cashback"

    def ready(self):
        from apps.core.signals import register_for_audit
        from .models import (
            CampanhaCashback,
            ConfiguracaoCashback,
            RegraCashbackCategoria,
            RegraCashbackEmpresa,
            RegraCashbackFilial,
            RegraCashbackProduto,
        )

        register_for_audit(ConfiguracaoCashback, "cashback")
        register_for_audit(RegraCashbackProduto, "cashback")
        register_for_audit(RegraCashbackCategoria, "cashback")
        register_for_audit(RegraCashbackFilial, "cashback")
        register_for_audit(RegraCashbackEmpresa, "cashback")
        register_for_audit(CampanhaCashback, "cashback")
