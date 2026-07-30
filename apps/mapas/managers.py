"""
Helpers de consulta geoespacial, aplicáveis a QUALQUER queryset cujo model
herde `CoordenadaMixin`.

São funções e não métodos de manager de propósito: assim `cadastros` e `core`
não precisam importar nada de `mapas`. A dependência aponta só num sentido
(mapas -> cadastros/core), o que evita import circular e mantém os módulos de
cadastro alheios à existência do mapa.
"""
from __future__ import annotations

from django.db.models import F

from apps.mapas import constants as c
from apps.mapas.expressions import DentroDaCaixa, anotar_distancia


def apenas_geocodificados(qs):
    """Só registros que já têm coordenada."""
    return qs.filter(latitude__isnull=False, longitude__isnull=False)


def com_distancia(qs, lat, lng):
    """Anota `distancia_m` (metros) até (lat, lng), sem filtrar."""
    return apenas_geocodificados(qs).annotate(distancia_m=anotar_distancia(lat, lng))


def no_raio(qs, lat, lng, raio_m, *, ordenar=True):
    """
    Registros dentro de `raio_m` metros de (lat, lng), anotados com
    `distancia_m` e ordenados por proximidade.

    Dois estágios de propósito:

    1. `DentroDaCaixa` é o predicado `earth_box(...) @> ll_to_earth(...)`, o
       único que o índice GIST por expressão atende. Ele recorta barato a
       maior parte da tabela.
    2. O filtro pela distância anotada corrige os cantos — a caixa é um
       superconjunto do círculo, então sem esse passo a busca devolveria
       pontos até ~41% além do raio na diagonal.

    Inverter a ordem daria o mesmo resultado, mas perderia o índice e viraria
    seq scan com cálculo de distância linha a linha.
    """
    raio_m = float(raio_m)
    qs = (
        apenas_geocodificados(qs)
        .annotate(distancia_m=anotar_distancia(lat, lng))
        .annotate(_na_caixa=DentroDaCaixa(lat, lng, raio_m))
        .filter(_na_caixa=True, distancia_m__lte=raio_m)
    )
    return qs.order_by('distancia_m') if ordenar else qs


def na_area(qs, sul, oeste, norte, leste):
    """
    Recorte pelo bounding box da viewport do mapa.

    Usa os índices B-tree comuns de latitude/longitude — não precisa do GIST,
    porque são duas comparações de range e não uma distância.
    """
    return apenas_geocodificados(qs).filter(
        latitude__gte=sul, latitude__lte=norte,
        longitude__gte=oeste, longitude__lte=leste,
    )


def pendentes_de_geocodificacao(qs):
    """
    Candidatos ao backfill, com os nunca geocodificados primeiro.

    O corte final é do `CoordenadaMixin.geo_desatualizado`, em Python: ele
    compara o hash do endereço atual com o que gerou a coordenada, e esse
    hash depende de concatenar/normalizar os campos. Reproduzir a mesma
    normalização em SQL duplicaria a regra e as duas versões divergiriam na
    primeira mudança de formato — então o SQL só pré-filtra o que é barato
    (não fixado à mão) e ordena por prioridade.
    """
    return qs.filter(geo_fixado=False).order_by(
        F('latitude').asc(nulls_first=True), 'pk',
    )


def limitar_marcadores(qs, limite=c.LIMITE_MARCADORES):
    """
    Corta a lista de marcadores e diz se houve corte.

    Devolve `(lista, truncado)`. Buscar limite+1 é o truque para saber que
    sobrou sem precisar de um COUNT(*) separado na tabela inteira.
    """
    itens = list(qs[: limite + 1])
    if len(itens) > limite:
        return itens[:limite], True
    return itens, False
