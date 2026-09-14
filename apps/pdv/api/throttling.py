"""Throttling da API de vendas do PDV -- mesmo padrao de
apps.produtos.api.throttling (SimpleRateThrottle por usuario, taxa
configuravel via settings). Uma venda e' um evento por carrinho fechado,
bem menos frequente que um bipe de codigo de barras, mas ainda merece
limite proprio: evita que um bug de retry no cliente (ou um script
malformado) martele `finalizar_venda` -- que grava linha em varias
tabelas e baixa estoque -- sem controle."""
from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class ThrottleVendaPDV(SimpleRateThrottle):
    scope = 'pdv_venda'

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}

    def get_rate(self):
        return f'{settings.PDV_VENDA_RATE_LIMIT}/min'
