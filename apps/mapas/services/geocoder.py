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
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

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
    detalhes: dict[str, Any] | None = None

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
                'countrycodes': 'br', 'addressdetails': 1,
            },
            headers={'User-Agent': self.user_agent, 'Accept-Language': 'pt-BR'},
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        resp.raise_for_status()
        dados = resp.json()
        if not dados:
            return Resultado(erro='endereco nao encontrado')

        item = dados[0]
        endereco_encontrado = item.get('address') or {}
        cep_solicitado = _cep_no_texto(endereco)
        cep_encontrado = re.sub(r'\D', '', str(endereco_encontrado.get('postcode') or ''))
        if cep_solicitado and cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        erro_numero = _validar_numero(endereco, endereco_encontrado.get('house_number'))
        if erro_numero:
            return Resultado(erro=erro_numero)
        erro_localidade = _validar_localidade(
            endereco,
            cidades=[endereco_encontrado.get(chave) for chave in (
                'city', 'town', 'village', 'municipality',
            )],
            uf=(endereco_encontrado.get('ISO3166-2-lvl4')
                or endereco_encontrado.get('state_code')
                or endereco_encontrado.get('state')),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
        return Resultado(
            latitude=float(item['lat']),
            longitude=float(item['lon']),
            precisao=_precisao_nominatim(item),
        )


class ArcGISGeocoder(GeocoderBase):
    """Geocodificador pontual do ArcGIS, usado como alternativa de precisão.

    A resposta só é aceita quando tem boa pontuação e, havendo CEP na busca,
    o CEP retornado é exatamente o solicitado. Isso evita o falso positivo em
    que uma busca por CEP encontra um estabelecimento chamado "Brasil" em
    outro bairro.
    """

    nome = 'arcgis'
    permite_uso_comercial = False

    def __init__(self, *, validar_numero: bool = True):
        self.validar_numero = validar_numero

    def geocodificar(self, endereco: str) -> Resultado:
        resp = requests.get(
            'https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates',
            params={
                'SingleLine': endereco, 'f': 'json', 'countryCode': 'BRA',
                'maxLocations': 1,
                'outFields': 'Match_addr,Addr_type,Postal,City,Subregion,Region,AddNum',
            },
            headers={'User-Agent': 'ERP-iNoovaTed/1.0'},
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        resp.raise_for_status()
        candidatos = resp.json().get('candidates') or []
        if not candidatos:
            return Resultado(erro='endereco nao encontrado')
        candidato = candidatos[0]
        if float(candidato.get('score') or 0) < 80:
            return Resultado(erro='resultado com baixa confiança')
        cep_solicitado = _cep_no_texto(endereco)
        cep_encontrado = re.sub(r'\D', '', str((candidato.get('attributes') or {}).get('Postal') or ''))
        if cep_solicitado and cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        atributos = candidato.get('attributes') or {}
        erro_numero = _validar_numero(endereco, atributos.get('AddNum'))
        if self.validar_numero and erro_numero:
            return Resultado(erro=erro_numero)
        erro_localidade = _validar_localidade(
            endereco,
            cidades=[atributos.get('City'), atributos.get('Subregion')],
            uf=atributos.get('Region'),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
        local = candidato.get('location') or {}
        if local.get('y') is None or local.get('x') is None:
            return Resultado(erro='coordenada ausente')
        tipo = str((candidato.get('attributes') or {}).get('Addr_type') or '').lower()
        return Resultado(
            latitude=float(local['y']), longitude=float(local['x']),
            precisao='exata' if tipo in ('pointaddress', 'subaddress') else 'aproximada',
            detalhes={
                'numero': str(atributos.get('AddNum') or '').strip(),
                'cep': cep_encontrado,
                'tipo': tipo,
                'pontuacao': float(candidato.get('score') or 0),
                'endereco': str(atributos.get('Match_addr') or candidato.get('address') or ''),
            },
        )


class BrasilApiCepGeocoder(GeocoderBase):
    """Localiza o CEP pela BrasilAPI v2 para validar resultados aproximados.

    O ponto representa o logradouro/CEP, não o número do imóvel. Por isso ele
    só é usado como referência ou fallback aproximado e nunca recebe precisão
    ``exata``.
    """

    nome = 'brasilapi_cep'
    permite_uso_comercial = True

    def geocodificar(self, endereco: str) -> Resultado:
        cep_solicitado = _cep_no_texto(endereco)
        if not cep_solicitado:
            return Resultado(erro='CEP não informado')
        resp = requests.get(
            f'https://brasilapi.com.br/api/cep/v2/{cep_solicitado}',
            headers={'User-Agent': 'ERP-iNoovaTed/1.0'},
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        if resp.status_code == 404:
            return Resultado(erro='CEP não encontrado')
        resp.raise_for_status()
        item = resp.json()
        cep_encontrado = re.sub(r'\D', '', str(item.get('cep') or ''))
        if cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        erro_localidade = _validar_localidade(
            endereco, cidades=[item.get('city')], uf=item.get('state'),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
        rua_solicitada = _normalizar_localidade(str(endereco).split(',', 1)[0])
        rua_encontrada = _normalizar_localidade(item.get('street'))
        if rua_solicitada and rua_encontrada and rua_solicitada != rua_encontrada:
            return Resultado(erro='resultado incompatível com a rua informada')
        coordenadas = ((item.get('location') or {}).get('coordinates') or {})
        try:
            latitude = float(coordenadas['latitude'])
            longitude = float(coordenadas['longitude'])
        except (KeyError, TypeError, ValueError):
            return Resultado(erro='CEP sem coordenada disponível')
        return Resultado(latitude, longitude, 'aproximada')


class AwesomeApiCepGeocoder(GeocoderBase):
    """Localiza o logradouro do CEP usando a base geográfica da AwesomeAPI.

    A BrasilAPI pode devolver coordenadas genéricas distantes do logradouro
    mesmo quando rua, bairro e município estão corretos. A resposta daqui
    também é validada contra esses campos antes de ser aceita pela rota.
    """

    nome = 'awesomeapi_cep'
    permite_uso_comercial = True

    def geocodificar(self, endereco: str) -> Resultado:
        cep_solicitado = _cep_no_texto(endereco)
        if not cep_solicitado:
            return Resultado(erro='CEP não informado')
        resp = requests.get(
            f'https://cep.awesomeapi.com.br/json/{cep_solicitado}',
            headers={'User-Agent': 'ERP-iNoovaTed/1.0'},
            timeout=c.GEOCODER_TIMEOUT_S,
        )
        if resp.status_code == 404:
            return Resultado(erro='CEP não encontrado')
        resp.raise_for_status()
        item = resp.json()
        cep_encontrado = re.sub(r'\D', '', str(item.get('cep') or ''))
        if cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        erro_localidade = _validar_localidade(
            endereco, cidades=[item.get('city')], uf=item.get('state'),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
        rua_solicitada = _normalizar_localidade(str(endereco).split(',', 1)[0])
        rua_encontrada = _normalizar_localidade(item.get('address'))
        if rua_solicitada and rua_encontrada and rua_solicitada != rua_encontrada:
            return Resultado(erro='resultado incompatível com a rua informada')
        try:
            latitude = float(item['lat'])
            longitude = float(item['lng'])
        except (KeyError, TypeError, ValueError):
            return Resultado(erro='CEP sem coordenada disponível')
        return Resultado(latitude, longitude, 'aproximada')


def _cep_no_texto(texto: str) -> str:
    encontrado = re.search(r'(?<!\d)(\d{5})-?(\d{3})(?!\d)', texto or '')
    return ''.join(encontrado.groups()) if encontrado else ''


def _numero_solicitado(endereco: str) -> str:
    partes = [parte.strip() for parte in str(endereco or '').split(',')]
    if len(partes) <= 1 or _cep_no_texto(partes[0]) or not re.search(r'\d', partes[1]):
        return ''
    return _normalizar_localidade(partes[1])


def _validar_numero(endereco: str, numero_encontrado) -> str:
    solicitado = _numero_solicitado(endereco)
    if not solicitado:
        return ''
    encontrado = _normalizar_localidade(numero_encontrado)
    if not encontrado:
        return 'resultado sem número verificável'
    if encontrado != solicitado:
        return 'resultado incompatível com o número informado'
    return ''


_UF_POR_NOME = {
    'acre': 'AC', 'alagoas': 'AL', 'amapa': 'AP', 'amazonas': 'AM',
    'bahia': 'BA', 'ceara': 'CE', 'distrito federal': 'DF',
    'espirito santo': 'ES', 'goias': 'GO', 'maranhao': 'MA',
    'mato grosso': 'MT', 'mato grosso do sul': 'MS', 'minas gerais': 'MG',
    'para': 'PA', 'paraiba': 'PB', 'parana': 'PR', 'pernambuco': 'PE',
    'piaui': 'PI', 'rio de janeiro': 'RJ', 'rio grande do norte': 'RN',
    'rio grande do sul': 'RS', 'rondonia': 'RO', 'roraima': 'RR',
    'santa catarina': 'SC', 'sao paulo': 'SP', 'sergipe': 'SE',
    'tocantins': 'TO',
}


def _normalizar_localidade(valor) -> str:
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    return re.sub(
        r'[^a-z0-9]+', ' ', texto.encode('ascii', 'ignore').decode().lower(),
    ).strip()


def _localidade_solicitada(endereco: str) -> tuple[str, str]:
    """Extrai município/UF do formato produzido por CoordenadaMixin."""
    partes = [parte.strip() for parte in str(endereco or '').split(',') if parte.strip()]
    uf_indice = next((
        indice for indice in range(len(partes) - 1, -1, -1)
        if re.fullmatch(r'[A-Za-z]{2}', partes[indice])
    ), None)
    if uf_indice is None:
        return '', ''
    cidade = partes[uf_indice - 1] if uf_indice > 0 else ''
    return _normalizar_localidade(cidade), partes[uf_indice].upper()


def _normalizar_uf(valor) -> str:
    normalizado = _normalizar_localidade(valor)
    if normalizado.startswith('br ') and len(normalizado) == 5:
        return normalizado[-2:].upper()
    if len(normalizado) == 2:
        return normalizado.upper()
    return _UF_POR_NOME.get(normalizado, '')


def _validar_localidade(endereco: str, *, cidades, uf) -> str:
    """Rejeita homônimos encontrados em outro município ou estado."""
    cidade_solicitada, uf_solicitada = _localidade_solicitada(endereco)
    cidades_encontradas = {
        normalizada for cidade in cidades
        if (normalizada := _normalizar_localidade(cidade))
    }
    uf_encontrada = _normalizar_uf(uf)
    if cidade_solicitada:
        if not cidades_encontradas:
            return 'resultado sem município verificável'
        if cidade_solicitada not in cidades_encontradas:
            return 'resultado incompatível com o município informado'
    if uf_solicitada:
        if not uf_encontrada:
            return 'resultado sem UF verificável'
        if uf_encontrada != uf_solicitada:
            return 'resultado incompatível com a UF informada'
    return ''


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
                'limit': 1, 'countrycodes': 'br', 'addressdetails': 1,
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
        endereco_encontrado = item.get('address') or {}
        cep_solicitado = _cep_no_texto(endereco)
        cep_encontrado = re.sub(r'\D', '', str(endereco_encontrado.get('postcode') or ''))
        if cep_solicitado and cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        erro_numero = _validar_numero(endereco, endereco_encontrado.get('house_number'))
        if erro_numero:
            return Resultado(erro=erro_numero)
        erro_localidade = _validar_localidade(
            endereco,
            cidades=[endereco_encontrado.get(chave) for chave in (
                'city', 'town', 'village', 'municipality',
            )],
            uf=(endereco_encontrado.get('ISO3166-2-lvl4')
                or endereco_encontrado.get('state_code')
                or endereco_encontrado.get('state')),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
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
        cep_solicitado = _cep_no_texto(endereco)
        cep_encontrado = re.sub(r'\D', '', str(item.get('postcode') or ''))
        if cep_solicitado and cep_encontrado != cep_solicitado:
            return Resultado(erro='resultado incompatível com o CEP informado')
        erro_numero = _validar_numero(endereco, item.get('housenumber'))
        if erro_numero:
            return Resultado(erro=erro_numero)
        erro_localidade = _validar_localidade(
            endereco,
            cidades=[item.get('city'), item.get('municipality'), item.get('county')],
            uf=item.get('state_code') or item.get('state'),
        )
        if erro_localidade:
            return Resultado(erro=erro_localidade)
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
                return Resultado(
                    cache.latitude, cache.longitude, cache.precisao,
                    detalhes=cache.detalhes or {},
                )
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
                'detalhes': res.detalhes or {},
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
