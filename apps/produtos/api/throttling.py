"""Throttling da API de produtos.

Por enquanto so no lookup de codigo de barras -- o endpoint mais chamado
pelo PDV (um bipe por item vendido, potencialmente varios por segundo em
horario de pico). Mesmo padrao de `apps.integracoes.throttling`
(SimpleRateThrottle com taxa configuravel via settings), mas por usuario
autenticado (sessao), nao por credencial de API.
"""
from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class ThrottleLookupCodigoBarras(SimpleRateThrottle):
    scope = 'produtos_lookup_codigo_barras'

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}

    def get_rate(self):
        return f'{settings.PRODUTOS_LOOKUP_RATE_LIMIT}/min'
