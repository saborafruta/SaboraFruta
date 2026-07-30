from django.apps import AppConfig


class MapasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.mapas'
    verbose_name = 'Mapas e Geolocalização'

    def ready(self):
        from apps.mapas import signals  # noqa: F401
