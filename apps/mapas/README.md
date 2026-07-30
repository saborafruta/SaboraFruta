# Módulo Mapas e Geolocalização

Documentação técnica. **Etapa 1 (fundação) entregue**; etapas 2 a 5 planejadas ao fim.

## 1. Decisões de arquitetura

### 1.1 Sem PostGIS — e por quê

A especificação original pedia PostGIS. Uma sondagem do banco de produção
(migration `core.0037_probe_capacidades_geo`, somente-leitura) mostrou:

```
PostgreSQL 18.4 (Debian) — 47 extensões disponíveis
postgis         → AUSENTE de pg_available_extensions
cube            → disponível
earthdistance   → disponível
pg_trgm         → disponível
```

`postgis` não está no `pg_available_extensions`, ou seja, `CREATE EXTENSION
postgis` **falharia** — o binário não existe na imagem do Postgres. Instalar
exigiria trocar a instância do banco de produção.

A alternativa adotada, `cube` + `earthdistance`, entrega o que o módulo
precisa e já está disponível:

| Necessidade | Como é resolvido |
|---|---|
| Distância geodésica em metros | `earth_distance(ll_to_earth(a), ll_to_earth(b))` |
| Busca por raio **com índice** | `earth_box(centro, raio) @> ll_to_earth(lat, lng)` + GIST |
| Recorte por viewport | B-tree composto em `(latitude, longitude)` |
| Polígonos (territórios) | Etapa 4 — atribuição pré-calculada, ver §5 |

Toda a álgebra espacial está isolada em `expressions.py` e `managers.py`. Se o
banco ganhar PostGIS, **são os dois únicos arquivos a mudar** — views, APIs,
serviços e templates não sabem qual motor está por baixo.

### 1.2 Geocodificação: provider trocável

As instâncias **públicas** do Nominatim, OSRM e VROOM **não servem para uso
comercial**:

- Nominatim público: proíbe geocodificação sistemática/em massa, limita a
  1 req/s e exige `User-Agent` identificável.
- OSRM `router.project-osrm.org`: declarado "for development only".
- VROOM: não há instância pública para produção.

Por isso o geocoder é injetado (`GeocoderBase`), com três implementações
(`nominatim`, `locationiq`, `geoapify`) e a flag `permite_uso_comercial`, que o
comando de backfill checa e avisa. Configuração:

```bash
MAPAS_GEOCODER=locationiq          # nominatim | locationiq | geoapify
MAPAS_GEOCODER_API_KEY=...
MAPAS_NOMINATIM_URL=https://...    # instância própria libera uso comercial
MAPAS_GEOCODER_USER_AGENT="ERP-iNoovaTed/1.0 (contato: ...)"
```

### 1.3 Geocodificar fora do request

O signal `pre_save` **não** chama o geocoder — apenas invalida a coordenada
quando o endereço muda. A chamada de rede fica no comando de backfill.

Motivo: uma requisição HTTP de até 10s, mais o throttle de 1,1s, dentro do
`save()` de um cliente. Numa importação de 500 clientes isso passaria de 10
minutos e estouraria o timeout de 120s do gunicorn — e uma queda do provider
passaria a **impedir o cadastro de clientes**. Invalidar é instantâneo e não
pode falhar.

## 2. Estrutura

```
apps/mapas/
├── constants.py           cores das camadas, raios, throttle, bbox do Brasil
├── expressions.py         Func do earthdistance (LlToEarth, EarthDistance, DentroDaCaixa)
├── managers.py            no_raio / na_area / com_distancia / limitar_marcadores
├── serializers.py         payload de marcador e de cliente próximo
├── signals.py             invalida coordenada quando o endereço muda
├── models/geocode.py      CacheGeocodificacao
├── services/
│   ├── geocoder.py        GeocoderBase + 3 providers + GeocodificacaoService
│   └── proximidade.py     ProximidadeService (raio, entrega)
├── views/
│   ├── mapa.py            MapaPrincipalView (+ cobertura §14)
│   └── api.py             camadas / clientes-proximos / cliente-detalhe
├── management/commands/geocodificar.py
├── migrations/
│   ├── 0001_initial.py                 CacheGeocodificacao
│   └── 0002_extensoes_e_indices_gist.py  cube, earthdistance, 12 índices
├── templates/mapas/mapa.html           Leaflet + MarkerCluster
└── tests/test_geo.py                   19 testes
```

Coordenadas nas entidades vêm de `apps.core.models.base.CoordenadaMixin`
(`latitude`, `longitude`, `geo_precisao`, `geo_atualizado_em`,
`geo_endereco_hash`, `geo_fixado`, `geo_erro`), herdado por **Cliente,
ClienteEndereco, Fornecedor, Transportadora, Motorista e Filial**.

O mixin vive em `core` e não em `mapas` de propósito: `cadastros` o herda, e
`mapas` consulta `cadastros`. Se o mixin estivesse em `mapas`, haveria import
circular. Pelo mesmo motivo os filtros geográficos são **funções** que recebem
um queryset, e não métodos de manager.

## 3. Fluxo da geocodificação

```
Usuário salva cliente
        │
        ▼
pre_save (signals.py) ── endereço mudou? ──não──► segue o save normal
        │ sim
        ▼
zera latitude/longitude/hash          (instantâneo, não pode falhar)
        │
        ▼
[ assíncrono ] manage.py geocodificar
        │
        ▼
GeocodificacaoService.resolver(endereco, hash)
        │
        ├─► CacheGeocodificacao tem o hash? ──sim──► devolve (0 chamadas)
        │
        ├─► falhou N vezes antes? ──sim──► desiste (não gasta quota)
        │
        ▼
throttle (1,1s) ──► provider HTTP ──► valida bbox do Brasil
        │
        ▼
grava CacheGeocodificacao (inclusive falha) + lat/lng na entidade
```

## 4. Fluxo da busca por raio

```
GET /mapas/api/clientes-proximos/?lat=&lng=&raio=
        │
        ▼
valida lat/lng (bbox Brasil) e limita raio a 50 km
        │
        ▼
escopo de filial (matriz = empresa toda; filial = só ela)   ← isolamento SaaS
        │
        ▼
┌─ managers.no_raio ────────────────────────────────────┐
│ 1. earth_box(centro, raio) @> ll_to_earth(lat, lng)   │ ← ÍNDICE GIST
│ 2. earth_distance(...) <= raio                        │ ← corrige os cantos
│ 3. ORDER BY distancia_m                               │
└───────────────────────────────────────────────────────┘
        │
        ▼
anexa RecompraCliente do CRM (1 query, sem N+1)
        │
        ▼
JSON: distância + última compra + dias sem comprar + valor médio + score
```

A ordem dos passos 1 e 2 importa: inverter dá o mesmo resultado mas perde o
índice e vira seq scan. O teste `test_consulta_usa_indice_gist` roda `EXPLAIN`
e falha se isso acontecer.

## 5. Banco de dados

```
┌──────────────────────┐
│ CoordenadaMixin      │  (abstract, em core.models.base)
│──────────────────────│
│ latitude      float  │──┐
│ longitude     float  │  │  índice GIST parcial:
│ geo_precisao         │  │  gist(ll_to_earth(lat,lng)) WHERE latitude NOT NULL
│ geo_atualizado_em    │  │  índice B-tree parcial: (latitude, longitude)
│ geo_endereco_hash ◄──┼──┼── liga com CacheGeocodificacao.endereco_hash
│ geo_fixado    bool   │  │   (por valor; não é FK, o cache é global)
│ geo_erro             │  │
└──────────┬───────────┘  │
           │ herdado por  │
   ┌───────┴────────┬─────┴─────┬──────────────┬───────────┬──────────┐
   ▼                ▼           ▼              ▼           ▼          ▼
clientes  clientes_enderecos fornecedores transportadoras motoristas filiais

┌───────────────────────────────┐
│ mapas_cache_geocodificacao    │  cache global, compartilhado entre empresas
│───────────────────────────────│  (um endereço é fato do mundo, não da empresa)
│ endereco_hash    PK           │
│ endereco_consultado           │
│ latitude / longitude          │
│ precisao / provider           │
│ encontrado  bool              │  guarda falhas para não reconsultar
│ tentativas  smallint          │
└───────────────────────────────┘
```

## 6. Uso

```bash
# backfill (uma vez, e depois por cron)
python manage.py geocodificar --dry-run          # o que faria
python manage.py geocodificar                    # tudo, lote de 200
python manage.py geocodificar --modelo cliente --limite 500
python manage.py geocodificar --forcar           # ignora o hash
```

APIs (todas exigem permissão `mapas`/`ver` e respeitam a filial ativa):

| Rota | Descrição |
|---|---|
| `GET /mapas/` | tela do mapa |
| `GET /mapas/api/camadas/?camadas=clientes,filiais&sul=&oeste=&norte=&leste=` | marcadores da viewport |
| `GET /mapas/api/clientes-proximos/?lat=&lng=&raio=` | §7 — clientes por proximidade |
| `GET /mapas/api/clientes/<pk>/` | dados do popup |

## 7. Performance

- Índice GIST parcial por expressão nas 6 tabelas (só linhas com coordenada).
- B-tree composto `(latitude, longitude)` para o recorte por viewport.
- Cache de geocodificação permanente — o recurso caro é a quota do provider.
- Marcadores limitados a 3.000 por camada, com aviso de truncamento; a carga é
  por viewport (lazy loading), não a base inteira.
- Payload de marcador enxuto; dados ricos do popup só no clique.
- MarkerCluster com `chunkedLoading`.
- `Count(filter=...)` numa query só para a cobertura, em vez de duas.

Pendente: **Redis não está provisionado** (o cache é LocMemCache e o broker do
Celery aponta para `localhost`). Onde há cache de fato hoje é no banco.

## 8. Etapas seguintes

| Etapa | Escopo | Pré-requisito |
|---|---|---|
| **2** | §8 sugestão ao faturar delivery (o `ProximidadeService.proximos_de_entrega` já está pronto); §10 heatmap; §14 dashboard completo | — |
| **3** | §4 rotas, §5 otimização, §6 distância ponto-a-ponto | **OSRM + VROOM self-hosted** (públicos vedam uso comercial) |
| **4** | §11 territórios, §12 geofence | Polígono em JSON + atribuição pré-calculada por cliente (point-in-polygon no app, recalculado quando o polígono muda) |
| **5** | §13 rastreamento em tempo real | **App de motorista** para enviar GPS (não existe) + polling, pois a produção é WSGI sem Channels |

`Praca` e `Rota` de `cadastros` hoje são textuais (cidades em texto,
motorista/veículo como `CharField`). A etapa 4 deve migrá-los para o modelo
geográfico em vez de criar entidades paralelas.

`Representante` não tem campos de endereço — só `regiao_atuacao` em texto. Para
colocá-lo no mapa (§1) é preciso decidir: endereço próprio, ou centroide dos
clientes dele.
