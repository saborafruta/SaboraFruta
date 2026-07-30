"""
Roteirização (§4) — distância, tempo e traçado de uma rota com várias paradas.

Provider trocável, pela mesma razão do geocoder: a instância pública do OSRM
(`router.project-osrm.org`) é declarada "for development only" e não serve para
uso comercial. As opções reais são uma instância própria de OSRM ou um provider
licenciado — o OpenRouteService tem plano gratuito que permite uso comercial e
fala OSM, então entra como implementação de primeira classe.

ARMADILHA que este módulo isola: OSRM e ORS recebem coordenadas em **lon,lat**,
enquanto Leaflet e o resto do sistema trabalham em **lat,lng**. Trocar a ordem
não gera erro — devolve uma rota em outro continente. Por isso a conversão
acontece num único lugar (`_para_lonlat`) e há teste dedicado.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: Teto de paradas por rota. Protege o tamanho da URL (OSRM recebe as
#: coordenadas no path) e o tempo de resposta.
MAX_PARADAS = 25
TIMEOUT_S = 20


@dataclass
class Parada:
    """Um ponto da rota, na ordem em que será visitado."""

    ordem: int
    nome: str
    lat: float
    lng: float
    cliente_id: int | None = None
    #: metros/segundos desde a parada anterior (0 na primeira)
    distancia_m: float = 0.0
    duracao_s: float = 0.0


@dataclass
class Rota:
    distancia_m: float = 0.0
    duracao_s: float = 0.0
    #: lista de [lat, lng] pronta para o L.polyline do Leaflet
    geometria: list = field(default_factory=list)
    paradas: list = field(default_factory=list)
    erro: str = ''

    @property
    def ok(self) -> bool:
        return not self.erro and bool(self.geometria)

    @property
    def distancia_km(self) -> float:
        return round(self.distancia_m / 1000, 2)

    @property
    def duracao_texto(self) -> str:
        minutos = int(round(self.duracao_s / 60))
        if minutos < 60:
            return f'{minutos} min'
        horas, resto = divmod(minutos, 60)
        return f'{horas}h{resto:02d}'


def _para_lonlat(pontos) -> str:
    """
    `[(lat, lng), ...]` -> `"lng,lat;lng,lat"`, o formato que OSRM espera.

    Único ponto do sistema que inverte a ordem. Inverter errado não levanta
    exceção: devolve uma rota plausível no lugar errado do mundo.
    """
    return ';'.join(f'{float(lng)},{float(lat)}' for lat, lng in pontos)


class RoteirizadorBase:
    nome = 'base'
    permite_uso_comercial = False

    def rota(self, pontos) -> Rota:  # pragma: no cover
        raise NotImplementedError


class OSRMRoteirizador(RoteirizadorBase):
    """
    OSRM. A instância pública é só para desenvolvimento; apontar
    `MAPAS_OSRM_URL` para uma instância própria libera o uso comercial.
    """

    nome = 'osrm'

    def __init__(self, base_url: str = ''):
        self.base_url = (base_url or 'https://router.project-osrm.org').rstrip('/')
        self.permite_uso_comercial = 'router.project-osrm.org' not in self.base_url

    def rota(self, pontos) -> Rota:
        url = f'{self.base_url}/route/v1/driving/{_para_lonlat(pontos)}'
        resp = requests.get(
            url,
            params={'overview': 'full', 'geometries': 'geojson', 'steps': 'false'},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        dados = resp.json()

        if dados.get('code') != 'Ok' or not dados.get('routes'):
            return Rota(erro=dados.get('message') or 'rota nao encontrada')

        rota = dados['routes'][0]
        # GeoJSON vem em lon,lat -> devolve em lat,lng para o Leaflet.
        geometria = [[lat, lng] for lng, lat in rota['geometry']['coordinates']]

        return Rota(
            distancia_m=float(rota.get('distance') or 0),
            duracao_s=float(rota.get('duration') or 0),
            geometria=geometria,
            paradas=[],  # preenchido por RoteirizacaoService, que tem os nomes
        )


class ORSRoteirizador(RoteirizadorBase):
    """
    OpenRouteService — plano gratuito com uso comercial permitido, base OSM.
    Requer `MAPAS_ROTA_API_KEY`.
    """

    nome = 'openrouteservice'
    permite_uso_comercial = True

    def __init__(self, api_key: str):
        self.api_key = api_key

    def rota(self, pontos) -> Rota:
        resp = requests.post(
            'https://api.openrouteservice.org/v2/directions/driving-car/geojson',
            headers={'Authorization': self.api_key,
                     'Content-Type': 'application/json'},
            json={'coordinates': [[float(lng), float(lat)] for lat, lng in pontos]},
            timeout=TIMEOUT_S,
        )
        if resp.status_code >= 400:
            try:
                msg = resp.json().get('error', {}).get('message', '')
            except ValueError:
                msg = ''
            return Rota(erro=msg or f'HTTP {resp.status_code}')

        dados = resp.json()
        features = dados.get('features') or []
        if not features:
            return Rota(erro='rota nao encontrada')

        resumo = (features[0].get('properties') or {}).get('summary') or {}
        coords = (features[0].get('geometry') or {}).get('coordinates') or []
        return Rota(
            distancia_m=float(resumo.get('distance') or 0),
            duracao_s=float(resumo.get('duration') or 0),
            geometria=[[lat, lng] for lng, lat in coords],
            paradas=[],
        )


def construir_roteirizador() -> RoteirizadorBase:
    """Instancia o provider configurado em settings."""
    nome = (getattr(settings, 'MAPAS_ROTA_PROVIDER', 'osrm') or 'osrm').lower()
    api_key = getattr(settings, 'MAPAS_ROTA_API_KEY', '')

    if nome in ('ors', 'openrouteservice'):
        if api_key:
            return ORSRoteirizador(api_key=api_key)
        logger.warning(
            'MAPAS_ROTA_PROVIDER=%s sem MAPAS_ROTA_API_KEY; caindo para OSRM.', nome,
        )
    return OSRMRoteirizador(base_url=getattr(settings, 'MAPAS_OSRM_URL', ''))


class RoteirizacaoService:
    """Monta a rota a partir de clientes selecionados."""

    def __init__(self, roteirizador: RoteirizadorBase | None = None):
        self.roteirizador = roteirizador or construir_roteirizador()

    @staticmethod
    def _escopo_filiais(filial):
        from apps.mapas.services.proximidade import ProximidadeService

        return ProximidadeService._escopo_filiais(filial)

    def rota_de_clientes(self, *, filial, cliente_ids, partir_da_filial=True) -> Rota:
        """
        Rota passando pelos clientes informados, **na ordem recebida**.

        Reordenar é o §5 (otimização) e é outro serviço: aqui a ordem é a que o
        usuário escolheu, senão ele não teria como montar um roteiro manual.

        Sai da filial ativa quando ela tem coordenada — é o ponto de partida
        real de uma entrega.
        """
        from apps.cadastros.models import Cliente

        if not cliente_ids:
            return Rota(erro='Selecione ao menos um cliente.')
        if len(cliente_ids) > MAX_PARADAS:
            return Rota(erro=f'Maximo de {MAX_PARADAS} paradas por rota.')

        encontrados = {
            c.pk: c for c in Cliente.objects.filter(
                pk__in=cliente_ids, filial__in=self._escopo_filiais(filial),
                latitude__isnull=False, longitude__isnull=False,
            )
        }
        # Preserva a ordem pedida: o filtro do ORM devolve em ordem arbitraria.
        clientes = [encontrados[cid] for cid in cliente_ids if cid in encontrados]
        if not clientes:
            return Rota(erro='Nenhum dos clientes selecionados tem coordenada.')

        pontos, paradas = [], []
        if partir_da_filial and getattr(filial, 'tem_coordenada', False):
            pontos.append((filial.latitude, filial.longitude))
            paradas.append(Parada(
                ordem=0,
                nome=f'{filial.nome_fantasia or filial.razao_social} (saida)',
                lat=filial.latitude, lng=filial.longitude,
            ))

        for cli in clientes:
            pontos.append((cli.latitude, cli.longitude))
            paradas.append(Parada(
                ordem=len(paradas),
                nome=cli.nome_fantasia or cli.razao_social or f'Cliente {cli.pk}',
                lat=cli.latitude, lng=cli.longitude, cliente_id=cli.pk,
            ))

        if len(pontos) < 2:
            return Rota(erro='A rota precisa de pelo menos dois pontos.')

        try:
            rota = self.roteirizador.rota(pontos)
        except requests.RequestException as exc:
            logger.warning('roteirizador %s falhou: %s', self.roteirizador.nome, exc)
            return Rota(erro=f'Falha no servico de rotas: {type(exc).__name__}')
        except Exception:
            logger.exception('erro inesperado no roteirizador')
            return Rota(erro='Erro inesperado no servico de rotas.')

        rota.paradas = paradas
        return rota
