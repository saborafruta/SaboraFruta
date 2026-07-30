"""
Expressões ORM sobre a extensão `earthdistance` do Postgres.

Por que earthdistance e não PostGIS: a instância do banco não tem o binário
do PostGIS disponível (`postgis` não aparece em `pg_available_extensions`),
mas `cube` + `earthdistance` estão — e entregam o que o módulo precisa:
distância geodésica em metros e, principalmente, um predicado de contenção
(`earth_box @> ll_to_earth`) que USA ÍNDICE GIST.

Toda a álgebra espacial do módulo passa por aqui. Se um dia o banco ganhar
PostGIS, é este arquivo (e só ele) que muda.
"""
from django.db.models import BooleanField, Func, Value


class LlToEarth(Func):
    """(lat, lng) -> ponto no modelo esférico da terra (tipo `earth`)."""

    function = 'll_to_earth'
    arity = 2


class EarthDistance(Func):
    """Distância geodésica entre dois pontos `earth`, em metros."""

    function = 'earth_distance'
    arity = 2


class EarthBox(Func):
    """Caixa envolvente de um ponto + raio (metros). Indexável por GIST."""

    function = 'earth_box'
    arity = 2


class DentroDaCaixa(Func):
    """
    `earth_box(centro, raio) @> ll_to_earth(lat, lng)`

    É o predicado que permite ao Postgres usar o índice GIST por expressão.
    Sem ele a consulta calcularia a distância de TODAS as linhas (seq scan);
    com ele, o índice recorta primeiro e a distância exata só roda no
    subconjunto. A caixa é um superconjunto do círculo, por isso o filtro
    exato de distância continua necessário depois.
    """

    template = '%(expressions)s'
    arg_joiner = ' @> '
    output_field = BooleanField()

    def __init__(self, lat, lng, raio_m):
        centro = LlToEarth(Value(float(lat)), Value(float(lng)))
        super().__init__(
            EarthBox(centro, Value(float(raio_m))),
            LlToEarth('latitude', 'longitude'),
        )


def anotar_distancia(lat, lng):
    """Expressão de distância em metros até (lat, lng), para annotate()."""
    return EarthDistance(
        LlToEarth(Value(float(lat)), Value(float(lng))),
        LlToEarth('latitude', 'longitude'),
    )
