from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class MobileLoginThrottle(SimpleRateThrottle):
    scope = 'mobile_login'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }

    def get_rate(self):
        return f'{settings.MOBILE_LOGIN_RATE_LIMIT}/min'


class MobileWriteThrottle(SimpleRateThrottle):
    scope = 'mobile_write'

    def get_cache_key(self, request, view):
        if request.method != 'POST' or not request.user.is_authenticated:
            return None
        filial = request.headers.get('X-Filial-ID', '')
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{request.user.pk}:{filial}',
        }

    def get_rate(self):
        return f'{settings.MOBILE_WRITE_RATE_LIMIT}/min'
