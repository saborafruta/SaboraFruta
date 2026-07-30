"""
Geocodificação de endereços, com provider trocável.

Decisão de arquitetura: o provider é uma dependência injetada, resolvida por
setting. Motivo prático — o Nominatim público **proíbe geocodificação em
massa** e o OSRM/VROOM de demonstração são "development only"; um ERP
comercial precisa de um provider licenciado (LocationIQ, Geoapify, MapTiler)
ou de instância própria. Trocar isso não pode significar reescrever o módulo,
então tudo fala com a interface `GeocoderBase`.

O `GeocodificacaoService` é quem o resto do sistema usa. Ele resolve na
ordem: cache do banco -> provider (com throttle) -> grava cache. Nunca
levanta exceção para fora: geocodificar é acessório, não pode derrubar o
cadastro de um cliente.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from django.utils import timezone

from apps.mapas import constants as c
from apps.mapas.models import CacheGeocodificacao

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resultado:
    """Retorno normalizado, igual para qualquer provider."""

    latitude: float | None = None
    longitude: float | None = None
    precisao: str = ''
    erro: str = ''

    @property
    def ok(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class _Throttle:
    """
    Espaçador de chamadas, por processo.

    Não é um rate limiter distribuído: o cache configurado é LocMemCache e o
    gunicorn roda 2 workers, então não há onde coordenar de forma confiável.
    Como a geocodificação em volume acontece pelo comando de backfill (um
    processo só), espaçar por processo é suficiente e honesto. Se um dia
    houver Redis, este é o ponto a trocar.
    """

    def __init__(self, intervalo_s: float):
        self._intervalo = intervalo_s
        self._lock = threading.Lock()
        self._ultima = 0.0

    def aguardar(self) -> None:
        with self._lock:
            espera = self._intervalo - (time.monotonic() - self._ultima)
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.monotonic()


class GeocoderBase:
    """Contrato de um provider de geocodificação."""

    nome = 'base'
    #: providers com política restritiva devem declarar aqui
    permite_uso_comercial = False

    def geocodificar(self, endereco: str) -> Resultado:  # pragma: no cover
        raise NotImplementedError


class NominatimGeocoder(GeocoderBase):
    """
    Nominatim (OpenStreetMap).

    ATENÇÃO: a instância pública (nominatim.openstreetmap.org) **veda uso
    sistemático/em massa** e exige User-Agent identificável e no máximo 1
    req/s. Serve para testes e para volume baixo. Em produção comercial,
    aponte `MAPAS_NOMINATIM_URL` para uma instância própria (aí o uso é
    livre) ou troque de provider.
    """

    nome = 'nominatim'
    permite_uso_comercial = False

    def __init__(self, base_url: str | None = None, user_agent: str = ''):
        self.base_url = (base_url or 'https://nominatim.openstreetmap.org').rstrip('/')
        self.user_agent = user_agent or 'ERP-iNoovaTed/1.0'
        # Instância própria não precisa do espaçamento da política pública.
        propria = 'nominatim.openstreetmap.org' not in self.base_url
        self.permite_uso_comercial = propria

    def geocodificar(self, endereco: str) -> Resultado:
        resp = requests.get(
            f'{self.base_url}/search',
            params={
                'q': endereco, 'format': 'jsonv2', 'limit': 1,
                'countrycodes': 'br', 'addressdetails': 0,
            },
            headers={'User-Agent': self.user_agent, 'Accept-Language': 'pt-BR'},
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        resp.raise_for_status()
        dados = resp.json()
        if not dados:
            return Resultado(erro='endereco nao encontrado')

        item = dados[0]
        return Resultado(
            latitude=float(item['lat']),
            longitude=float(item['lon']),
            precisao=_precisao_nominatim(item),
        )


class LocationIQGeocoder(GeocoderBase):
    """LocationIQ — API compatível com Nominatim, com plano gratuito que
    permite uso comercial. Requer `MAPAS_GEOCODER_API_KEY`."""

    nome = 'locationiq'
    permite_uso_comercial = True

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocodificar(self, endereco: str) -> Resultado:
        resp = requests.get(
            'https://us1.locationiq.com/v1/search',
            params={
                'key': self.api_key, 'q': endereco, 'format': 'json',
                'limit': 1, 'countrycodes': 'br',
            },
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        if resp.status_code == 404:
            return Resultado(erro='endereco nao encontrado')
        resp.raise_for_status()
        dados = resp.json()
        if not dados:
            return Resultado(erro='endereco nao encontrado')
        item = dados[0]
        return Resultado(
            latitude=float(item['lat']),
            longitude=float(item['lon']),
            precisao=_precisao_nominatim(item),
        )


class GeoapifyGeocoder(GeocoderBase):
    """Geoapify — plano gratuito com uso comercial permitido."""

    nome = 'geoapify'
    permite_uso_comercial = True

    def __init__(self, api_key: str):
        self.api_key = api_key

    def geocodificar(self, endereco: str) -> Resultado:
        resp = requests.get(
            'https://api.geoapify.com/v1/geocode/search',
            params={
                'text': endereco, 'filter': 'countrycode:br',
                'limit': 1, 'format': 'json', 'apiKey': self.api_key,
            },
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        resp.raise_for_status()
        itens = resp.json().get('results') or []
        if not itens:
            return Resultado(erro='endereco nao encontrado')
        item = itens[0]
        rank = (item.get('rank') or {}).get('match_type', '')
        precisao = {
            'full_match': 'exata',
            'inner_part': 'aproximada',
        }.get(rank, 'aproximada')
        return Resultado(
            latitude=float(item['lat']),
            longitude=float(item['lon']),
            precisao=precisao,
        )


def _precisao_nominatim(item: dict) -> str:
    """Traduz a categoria do Nominatim/LocationIQ para a nossa escala."""
    tipo = (item.get('type') or '').lower()
    classe = (item.get('class') or '').lower()
    if tipo in ('house', 'building', 'residential') or classe == 'building':
        return 'exata'
    if tipo in ('city', 'town', 'municipality', 'administrative'):
        return 'cidade'
    return 'aproximada'


_PROVIDERS = {
    'nominatim': NominatimGeocoder,
    'locationiq': LocationIQGeocoder,
    'geoapify': GeoapifyGeocoder,
}


def construir_geocoder() -> GeocoderBase:
    """Instancia o provider configurado em settings."""
    nome = getattr(settings, 'MAPAS_GEOCODER', 'nominatim').lower()
    api_key = getattr(settings, 'MAPAS_GEOCODER_API_KEY', '')

    if nome in ('locationiq', 'geoapify'):
        if not api_key:
            logger.warning(
                'MAPAS_GEOCODER=%s sem MAPAS_GEOCODER_API_KEY; caindo para nominatim.',
                nome,
            )
        else:
            return _PROVIDERS[nome](api_key=api_key)

    return NominatimGeocoder(
        base_url=getattr(settings, 'MAPAS_NOMINATIM_URL', ''),
        user_agent=getattr(settings, 'MAPAS_GEOCODER_USER_AGENT', ''),
    )


class GeocodificacaoService:
    """Orquestra cache + provider + persistência na entidade."""

    def __init__(self, geocoder: GeocoderBase | None = None, throttle: _Throttle | None = None):
        self.geocoder = geocoder or construir_geocoder()
        self.throttle = throttle or _Throttle(c.GEOCODER_INTERVALO_S)

    # ------------------------------------------------------------ endereço
    def resolver(self, endereco: str, endereco_hash: str) -> Resultado:
        """Coordenada de um endereço, consultando o cache antes do provider."""
        if not endereco:
            return Resultado(erro='endereco vazio')

        cache = CacheGeocodificacao.objects.filter(pk=endereco_hash).first()
        if cache is not None:
            if cache.encontrado:
                return Resultado(cache.latitude, cache.longitude, cache.precisao)
            if cache.tentativas >= c.GEOCODER_MAX_TENTATIVAS:
                # Já falhou o suficiente: não gasta mais quota com ele.
                return Resultado(erro=cache.erro or 'endereco nao encontrado')

        self.throttle.aguardar()
        try:
            res = self.geocoder.geocodificar(endereco)
        except requests.RequestException as exc:
            # Falha de rede/quota não deve virar cache negativo permanente.
            logger.warning('geocoder %s falhou para %r: %s', self.geocoder.nome, endereco, exc)
            return Resultado(erro=f'falha no provider: {type(exc).__name__}')
        except Exception:
            logger.exception('erro inesperado no geocoder para %r', endereco)
            return Resultado(erro='erro inesperado no provider')

        if res.ok and not c.dentro_do_brasil(res.latitude, res.longitude):
            # Endereço ambíguo resolvido no exterior ("Natal" -> África do Sul).
            res = Resultado(erro='coordenada fora do Brasil')

        self._gravar_cache(endereco, endereco_hash, res, cache)
        return res

    def _gravar_cache(self, endereco, endereco_hash, res: Resultado, cache) -> None:
        CacheGeocodificacao.objects.update_or_create(
            pk=endereco_hash,
            defaults={
                'endereco_consultado': endereco[:300],
                'latitude': res.latitude,
                'longitude': res.longitude,
                'precisao': res.precisao,
                'provider': self.geocoder.nome,
                'encontrado': res.ok,
                'erro': res.erro[:160],
                'tentativas': (cache.tentativas + 1) if cache else 1,
            },
        )

    # ------------------------------------------------------------ entidade
    def geocodificar_objeto(self, obj, *, salvar: bool = True) -> bool:
        """
        Preenche lat/lng de qualquer instância com `CoordenadaMixin`.

        Devolve True se gravou coordenada. Respeita `geo_fixado` (coordenada
        ajustada à mão nunca é sobrescrita).
        """
        if getattr(obj, 'geo_fixado', False):
            return False

        endereco = obj.endereco_para_geocodificar()
        endereco_hash = obj.hash_endereco_atual()
        if not endereco_hash:
            return False

        res = self.resolver(endereco, endereco_hash)

        obj.geo_endereco_hash = endereco_hash
        obj.geo_atualizado_em = timezone.now()
        campos = ['geo_endereco_hash', 'geo_atualizado_em', 'geo_erro']

        if res.ok:
            obj.latitude = res.latitude
            obj.longitude = res.longitude
            obj.geo_precisao = res.precisao
            obj.geo_erro = ''
            campos += ['latitude', 'longitude', 'geo_precisao']
        else:
            obj.geo_erro = res.erro[:160]

        if salvar:
            obj.save(update_fields=campos)
        return res.ok
