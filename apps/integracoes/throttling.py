from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class LimitePorCredencial(SimpleRateThrottle):
    scope = 'integracao_api'

    def get_cache_key(self, request, view):
        credencial = getattr(request, 'auth', None)
        if not credencial:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': credencial.pk}

    def get_rate(self):
        return f'{settings.INTEGRACAO_API_RATE_LIMIT}/min'
