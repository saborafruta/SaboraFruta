from django.apps import AppConfig


class FoodServiceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.food_service"
    verbose_name = "Food Service"

    def ready(self):
        from apps.core.signals import register_for_audit
        from .models import Comanda, Mesa

        register_for_audit(Mesa, "food_service")
        register_for_audit(Comanda, "food_service")
