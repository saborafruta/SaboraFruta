"""
Otimização da ordem das entregas (§5).

A especificação pede VROOM. O endpoint `/optimization` do OpenRouteService **é
o VROOM** hospedado, então a mesma chave que habilita as rotas (§4) habilita
isto — sem subir mais um serviço. Quem tiver VROOM próprio aponta
`MAPAS_VROOM_URL`.

Quando não há provider configurado, cai num otimizador **local** (vizinho mais
próximo + 2-opt). Ele não é ótimo e não conhece as ruas — trabalha em linha
reta —, mas resolve a maior parte do ganho num roteiro de 5 a 20 paradas e
funciona sem infraestrutura nenhuma. O resultado diz qual estratégia foi usada,
para a tela não vender como "otimizado por VROOM" o que foi heurística local.

O ganho é sempre medido **roteirizando as duas ordens** (antes e depois) no
mesmo provider de rotas. Comparar distância em linha reta daria um número
bonito e errado: o que interessa ao motorista é a quilometragem por rua.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_S = 25
RAIO_TERRA_M = 6_371_000


@dataclass
class Otimizacao:
    """Comparação entre a ordem original e a otimizada."""

    ordem_antes: list = field(default_factory=list)
    ordem_depois: list = field(default_factory=list)
    rota_antes: object = None
    rota_depois: object = None
    estrategia: str = ''
    erro: str = ''

    @property
    def ok(self) -> bool:
        return not self.erro and self.rota_depois is not None

    @property
    def economia_m(self) -> float:
        if not self.rota_antes or not self.rota_depois:
            return 0.0
        return max(0.0, self.rota_antes.distancia_m - self.rota_depois.distancia_m)

    @property
    def economia_s(self) -> float:
        if not self.rota_antes or not self.rota_depois:
            return 0.0
        return max(0.0, self.rota_antes.duracao_s - self.rota_depois.duracao_s)

    @property
    def economia_km(self) -> float:
        return round(self.economia_m / 1000, 2)

    @property
    def economia_texto(self) -> str:
        minutos = int(round(self.economia_s / 60))
        if minutos < 60:
            return f'{minutos} min'
        horas, resto = divmod(minutos, 60)
        return f'{horas}h{resto:02d}'

    @property
    def melhorou(self) -> bool:
        return self.economia_m > 1 or self.economia_s > 30


def distancia_haversine_m(a, b) -> float:
    """Distância em linha reta entre `(lat, lng)`, em metros."""
    lat1, lng1 = radians(a[0]), radians(a[1])
    lat2, lng2 = radians(b[0]), radians(b[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * RAIO_TERRA_M * asin(sqrt(h))


def _custo_total(pontos) -> float:
    return sum(
        distancia_haversine_m(pontos[i], pontos[i + 1])
        for i in range(len(pontos) - 1)
    )


def otimizar_local(pontos, fixar_primeiro=True) -> list:
    """
    Ordem melhorada por vizinho mais próximo + 2-opt, em linha reta.

    Devolve os ÍNDICES na nova ordem. `fixar_primeiro` mantém o ponto de
    partida (a filial) na posição inicial — ele não é uma entrega a reordenar.

    2-opt sobre nearest-neighbour é o par clássico para roteiro pequeno: o
    vizinho mais próximo dá uma solução rápida mas com cruzamentos, e o 2-opt
    desfaz justamente esses cruzamentos invertendo trechos.
    """
    n = len(pontos)
    if n <= 2:
        return list(range(n))

    inicio = 0 if fixar_primeiro else 0
    restantes = set(range(n)) - {inicio}
    ordem = [inicio]

    # Vizinho mais próximo
    atual = inicio
    while restantes:
        proximo = min(restantes, key=lambda i: distancia_haversine_m(pontos[atual], pontos[i]))
        ordem.append(proximo)
        restantes.remove(proximo)
        atual = proximo

    # 2-opt: inverte trechos enquanto houver ganho. O primeiro ponto nunca
    # entra na inversão quando `fixar_primeiro`.
    limite_inferior = 1 if fixar_primeiro else 0
    melhorou = True
    while melhorou:
        melhorou = False
        for i in range(limite_inferior, len(ordem) - 1):
            for j in range(i + 1, len(ordem)):
                candidata = ordem[:i] + ordem[i:j + 1][::-1] + ordem[j + 1:]
                if _custo_total([pontos[k] for k in candidata]) < _custo_total(
                    [pontos[k] for k in ordem]
                ) - 0.5:
                    ordem = candidata
                    melhorou = True
    return ordem


class OtimizadorBase:
    nome = 'base'
    permite_uso_comercial = False

    def ordenar(self, pontos, fixar_primeiro=True) -> list:  # pragma: no cover
        raise NotImplementedError


class OtimizadorLocal(OtimizadorBase):
    """Heurística em linha reta, sem rede. Sempre disponível."""

    nome = 'local'
    permite_uso_comercial = True

    def ordenar(self, pontos, fixar_primeiro=True) -> list:
        return otimizar_local(pontos, fixar_primeiro=fixar_primeiro)


class VROOMOtimizador(OtimizadorBase):
    """
    VROOM — direto numa instância própria (`MAPAS_VROOM_URL`) ou via o endpoint
    `/optimization` do OpenRouteService, que é VROOM hospedado.
    """

    nome = 'vroom'

    def __init__(self, url: str = '', api_key: str = ''):
        self.url = (url or 'https://api.openrouteservice.org/optimization').rstrip('/')
        self.api_key = api_key
        self.via_ors = 'openrouteservice.org' in self.url
        self.nome = 'vroom (openrouteservice)' if self.via_ors else 'vroom'
        # Instância própria não tem limite de política; o ORS gratuito permite
        # uso comercial.
        self.permite_uso_comercial = True

    def ordenar(self, pontos, fixar_primeiro=True) -> list:
        # VROOM fala lon,lat — mesma inversão do OSRM (ver services.roteirizacao).
        def lonlat(p):
            return [float(p[1]), float(p[0])]

        inicio_idx = 0 if fixar_primeiro else None
        entregas = [
            {'id': i, 'location': lonlat(p)}
            for i, p in enumerate(pontos)
            if i != inicio_idx
        ]
        veiculo = {'id': 1, 'profile': 'driving-car'}
        if inicio_idx is not None:
            veiculo['start'] = lonlat(pontos[inicio_idx])

        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = self.api_key

        resp = requests.post(
            self.url,
            headers=headers,
            json={'jobs': entregas, 'vehicles': [veiculo]},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        dados = resp.json()

        rotas = dados.get('routes') or []
        if not rotas:
            raise ValueError('VROOM nao devolveu rota')

        ordem = [inicio_idx] if inicio_idx is not None else []
        for passo in rotas[0].get('steps', []):
            if passo.get('type') == 'job' and passo.get('id') is not None:
                ordem.append(int(passo['id']))

        # Defensivo: se o provider devolver menos paradas que o pedido, o
        # roteiro sairia incompleto e o usuario perderia entregas.
        if len(ordem) != len(pontos):
            raise ValueError('VROOM devolveu um numero de paradas diferente do enviado')
        return ordem


def construir_otimizador() -> OtimizadorBase:
    """
    VROOM quando houver como falar com ele; senão, heurística local.

    A chave do §4 (`MAPAS_ROTA_API_KEY`) serve aqui: no ORS, rotas e otimização
    são o mesmo produto.
    """
    url = getattr(settings, 'MAPAS_VROOM_URL', '')
    api_key = getattr(settings, 'MAPAS_ROTA_API_KEY', '')

    if url:
        return VROOMOtimizador(url=url, api_key=api_key)
    if api_key:
        return VROOMOtimizador(api_key=api_key)
    return OtimizadorLocal()


class OtimizacaoService:
    """Reordena as paradas e mede o ganho real, por rua."""

    def __init__(self, otimizador: OtimizadorBase | None = None, roteirizacao=None):
        from apps.mapas.services.roteirizacao import RoteirizacaoService

        self.otimizador = otimizador or construir_otimizador()
        self.roteirizacao = roteirizacao or RoteirizacaoService()

    def otimizar(self, *, filial, cliente_ids, partir_da_filial=True) -> Otimizacao:
        """
        Compara a ordem informada com a otimizada.

        Roteiriza as duas para medir o ganho em quilometragem de rua, e não em
        linha reta. Se a ordem otimizada não for melhor, devolve a original —
        mostrar "economia negativa" seria pior que não otimizar.
        """
        rota_antes = self.roteirizacao.rota_de_clientes(
            filial=filial, cliente_ids=cliente_ids, partir_da_filial=partir_da_filial,
        )
        if not rota_antes.ok:
            return Otimizacao(erro=rota_antes.erro or 'Nao foi possivel roteirizar a ordem atual.')

        paradas = rota_antes.paradas
        pontos = [(p.lat, p.lng) for p in paradas]
        tem_origem = bool(paradas) and paradas[0].cliente_id is None

        try:
            ordem = self.otimizador.ordenar(pontos, fixar_primeiro=tem_origem)
            estrategia = self.otimizador.nome
        except Exception as exc:
            # Provider fora do ar nao pode deixar o usuario sem otimizacao:
            # cai para a heuristica local e diz na resposta o que aconteceu.
            logger.warning('otimizador %s falhou (%s); usando local',
                           self.otimizador.nome, exc)
            ordem = otimizar_local(pontos, fixar_primeiro=tem_origem)
            estrategia = 'local (fallback)'

        ids_otimizados = [
            paradas[i].cliente_id for i in ordem if paradas[i].cliente_id is not None
        ]
        rota_depois = self.roteirizacao.rota_de_clientes(
            filial=filial, cliente_ids=ids_otimizados, partir_da_filial=partir_da_filial,
        )
        if not rota_depois.ok:
            return Otimizacao(erro=rota_depois.erro or 'Nao foi possivel roteirizar a nova ordem.')

        resultado = Otimizacao(
            ordem_antes=[p.cliente_id for p in paradas if p.cliente_id is not None],
            ordem_depois=ids_otimizados,
            rota_antes=rota_antes,
            rota_depois=rota_depois,
            estrategia=estrategia,
        )

        # Sem ganho: mantem a ordem do usuario. Reordenar sem motivo so
        # confundiria quem montou o roteiro de proposito.
        if not resultado.melhorou:
            resultado.ordem_depois = resultado.ordem_antes
            resultado.rota_depois = rota_antes
        return resultado
