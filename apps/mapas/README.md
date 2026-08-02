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

| Etapa | Escopo | Situação |
|---|---|---|
| **1** | Fundação: coordenadas, geocodificação, mapa, clientes próximos | ✅ entregue |
| **4** | §11 territórios — `Praca` evoluída para geográfica, `Rota` com FKs | ✅ entregue |
| **3a** | §4 rotas — selecionar clientes, distância, tempo, traçado e paradas | ✅ entregue |
| **2** | §8 sugestão ao faturar delivery (`proximos_de_entrega` já pronto); §10 heatmap; §14 dashboard | a fazer |
| **3b** | §5 otimização (reordenar) | ✅ entregue |
| **3c** | §6 distância ponto-a-ponto | ✅ entregue |
| **5** | §12 geofence + §13 rastreamento | **stand by** — depende de app de motorista |

### Etapa 4 — o que foi feito

`Praca` **evoluiu** para território geográfico em vez de ganhar entidade
paralela. Os dois critérios convivem: `cidades` (texto, por município)
continua valendo para precificação, e `poligono` dá delimitação precisa.

- `Praca.poligono` — JSON de `[[lat, lng], ...]` (ordem do Leaflet, não do
  GeoJSON, porque é o Leaflet quem lê e escreve).
- `Praca.bbox_*` — caixa envolvente materializada. Sem ela, achar os clientes
  de um território varreria a base inteira; com ela o índice B-tree recorta
  primeiro e só os candidatos passam pelo ray casting.
- `definir_poligono()` grava polígono e bbox **juntos** — se divergirem, a
  atribuição ignora parte do território sem dar erro. Por isso o polígono é
  excluído do `PracaForm`: só a API o grava.
- `mapas.ClienteTerritorio` — atribuição materializada (delete + bulk_create no
  recálculo). Fica em `mapas`, não como FK em `Cliente`, para manter a direção
  da dependência.

`Rota` passou de texto para FK: `motorista` → `Motorista`, `veiculo` →
`Veiculo`. A migration `cadastros.0014` casa os textos antigos com os
cadastros (nome normalizado / placa alfanumérica). **O que não casar é
mantido no texto legado de propósito** — pode ser terceirizado ou erro de
digitação, e apagar destruiria a única informação. As properties
`motorista_nome`/`veiculo_placa` exibem a FK e caem no texto quando não há.

> Ao subir, procure no log a linha `[rotas] N de M rota(s) vinculadas`. A
> diferença são as rotas que ficaram só com o texto — vale conferir.

**Atenção de segurança corrigida aqui:** `ModelForm` com `exclude` cria os
selects de FK com queryset de *todos* os registros. Num SaaS multiempresa isso
listaria motoristas e representantes de outros inquilinos. O helper `_escopar`
em `forms/rota_praca.py` resolve, e há testes (`FormEscopoTests`) que falham se
alguém adicionar uma FK sem escopo.

### §4 — Rotas

Botão **“Criar rota”** no mapa → clique nos clientes (a ordem dos cliques é a
ordem das paradas) → **Criar rota**. Volta distância total, tempo estimado, o
traçado desenhado no mapa e a lista de paradas numerada.

- No modo rota o clique no pino **monta o roteiro em vez de abrir o popup** —
  com vários clientes em sequência, um popup por clique atrapalharia.
- Sai da filial ativa quando ela tem coordenada (o ponto de partida real de uma
  entrega). Desmarcável.
- Teto de 25 paradas: o OSRM recebe as coordenadas no path da URL.
- A ordem é a que o usuário montou. **Reordenar é o §5** e será outro serviço.

**A armadilha deste módulo:** OSRM e ORS recebem coordenadas em `lon,lat`,
enquanto Leaflet e o resto do sistema usam `lat,lng`. Inverter não levanta erro
— devolve uma rota plausível no lugar errado do mundo. A conversão está isolada
em `_para_lonlat()` e há teste dedicado nos dois sentidos.

Configuração (`MAPAS_ROTA_PROVIDER`):

| Valor | Quando usar |
|---|---|
| `osrm` (padrão) | Instância pública — **só testes**, é "development only" |
| `osrm` + `MAPAS_OSRM_URL` | Instância própria: uso comercial liberado |
| `openrouteservice` + `MAPAS_ROTA_API_KEY` | Plano gratuito com uso comercial permitido |

A resposta da API traz `uso_comercial_liberado`; quando é `false`, o painel
avisa na tela que aquele servidor não serve para produção.

### §5 — Otimização (VROOM)

Botão **“Otimizar Ordem”** no painel de rota. Mostra **antes** (riscado),
**depois**, e os **km + tempo economizados**.

O ganho é medido **roteirizando as duas ordens** no mesmo provider. Comparar
distância em linha reta daria um número bonito e errado: o motorista anda por
rua, não em linha reta.

Estratégias, em ordem de preferência (`construir_otimizador`):

| Condição | Estratégia |
|---|---|
| `MAPAS_VROOM_URL` | VROOM próprio |
| `MAPAS_ROTA_API_KEY` | **VROOM via ORS** — `/optimization` do OpenRouteService É o VROOM |
| nada configurado | heurística local: vizinho mais próximo + 2-opt |

A heurística local não conhece as ruas (trabalha em linha reta) e não é ótima,
mas entrega a maior parte do ganho num roteiro de 5–20 paradas e **funciona sem
infraestrutura**. A resposta traz `estrategia`, e a tela avisa quando o
resultado veio da heurística — para não vender como “otimizado por VROOM” o que
não foi.

Duas decisões de segurança:

- **Sem ganho, a ordem do usuário é mantida.** Reordenar sem motivo confundiria
  quem montou o roteiro de propósito.
- **Provider fora do ar cai para a heurística local** em vez de deixar o usuário
  sem o recurso; a estratégia reportada vira `local (fallback)`.
- Se o VROOM devolver menos paradas do que foram enviadas, o resultado é
  **recusado** — aceitar faria o usuário perder entregas silenciosamente.

### §6 — Distância entre cadastros

Widget reutilizável. Basta incluir numa tela de cadastro já salva:

```django
{% include "mapas/_widget_distancia.html" with origem_tipo="cliente" origem_id=cliente.pk %}
```

Tipos: `cliente`, `fornecedor`, `transportadora`, `motorista`, `filial`.
Já está no formulário de Cliente; incluir nos demais é essa uma linha.

Mostra **distância**, **tempo** e um botão **“Ver rota no mapa”**.

**O widget não carrega Leaflet de propósito.** Uma tela de cadastro não deveria
pagar o custo de um mapa completo por causa de um bloco lateral — o traçado
abre no mapa principal, que já tem tudo carregado, via
`/mapas/?de_tipo=...&para_tipo=...`.

Usa o mesmo provider de rotas do §4: se usasse outro caminho, a distância no
cadastro poderia divergir da mostrada no mapa para o mesmo par de pontos.

Dois cuidados:

- O seletor de destino **só lista quem tem coordenada** — oferecer um destino
  sem coordenada garantiria um erro no passo seguinte.
- A resposta traz a **linha reta** junto. Quando a rota por rua passa de ~2,5x
  a reta, a tela avisa: costuma ser rio/serra no caminho, ou endereço
  geocodificado errado. Sem isso, o usuário decidiria em cima de um número que
  parece certo.

### §7 — Clientes próximos

`GET /mapas/api/clientes-proximos/?lat=&lng=&raio=[&excluir_cliente=]`

Devolve os clientes do mais perto para o mais longe, já com `distancia_texto`
formatado (`320 m`, `1,2 km`) — a tela não deveria ter de decidir entre metros
e km. Cada cliente vem enriquecido com os indicadores do CRM (última compra,
dias sem comprar, valor médio, score): proximidade sozinha não diz se vale a
pena bater na porta.

```json
{"centro": {"lat": -5.79, "lng": -35.21}, "raio_m": 3000, "total": 3,
 "clientes": [{"nome": "Cliente A", "distancia_m": 320, "distancia_texto": "320 m", ...}]}
```

O trabalho fica no banco (`earth_box` + índice GIST), não em Python — ver §4
acima. Teto de **50 km** de raio e **100 registros**; ambos aparecem na
resposta (`raio_m`) ou na tela, porque um limite silencioso faria o usuário ler
"100 clientes" como "todos os clientes da área".

**Na tela:** botão *Clientes próximos* no mapa. Clique num ponto (ou num pino)
e escolha o raio — 1/3/5/10 km. O círculo desenhado mostra até onde a busca
olhou: sem ele, uma lista curta se confunde com "não tem cliente aqui" quando o
raio é que estava pequeno. Clicando num cliente, ele sai da própria lista — a
0 m ele não acrescentaria nada.

### §8 — Sugestão ao sair para entrega

`GET /mapas/api/sugestao-entrega/<venda_pk>/?raio=`

Quando um pedido de delivery **sai para entrega**, o Kanban abre sozinho uma
janela com os clientes ao redor do endereço: nome, distância, última compra,
dias sem comprar, valor médio e frequência — e três ações por linha: **Criar
oferta** (abre o PDV com o cliente já selecionado), **WhatsApp** e **+ Rota**.

**Por que ao sair para entrega, e não ao faturar.** Quando o pedido chega ao
Kanban a venda já está fechada; o momento em que a sugestão ainda dá para usar
é enquanto o entregador não saiu. Depois que ele voltou, saber quem estava
perto do trajeto não serve para nada. Há também um botão no card, para olhar
antes de despachar ou conferir de novo depois.

A permissão exigida é a do **PDV**, não a de mapas: quem consome é o Kanban, e
um operador de balcão costuma não ter acesso ao módulo de mapas — exigir
`mapas.ver` esconderia a sugestão justamente de quem está com o pedido na mão.
O dado exposto (clientes da própria filial) esse operador já alcança pela busca
do PDV.

A coordenada da entrega sai, nesta ordem, do `endereco_entrega` da venda (o
operador pode ter ajustado o ponto no checkout) e depois do cadastro do
cliente. Sem nenhuma das duas, a API responde **200 com `motivo`**, não um
erro: a saída é geocodificar o cliente, e um 4xx viraria só um "falhou"
genérico na tela.

**+ Rota** manda os ids escolhidos para `/mapas/?rota=1,2,3`. Só os ids viajam
na URL — os nomes o mapa resolve por `api/distancia/destinos/?ids=`, senão um
link com meia dúzia de razões sociais fica enorme e quebra ao ser copiado.

### §10 — Mapa de calor

`GET /mapas/api/heatmap/?metrica=&de=&ate=&cidade=&uf=&representante=&filial=`

Quatro métricas: **receita**, **quantidade de pedidos**, **volume vendido** e
**número de clientes**. Filtros de cidade, estado, representante, filial e
período. Botão *Mapa de calor* no mapa.

**O ponto de calor é a coordenada do cliente**, não a do endereço de entrega: a
do cadastro é a que o backfill geocodifica e mantém; a de entrega é um JSON por
venda que quase nunca traz lat/lng. O resultado é uma leitura estável de "de
onde vem meu dinheiro".

**Soma as duas origens de venda** — `PedidoVenda` (B2B) e `VendaPDV` (balcão e
delivery) —, como o dashboard e a Curva ABC já fazem. Usar só uma mostraria
metade do faturamento, e o mapa continuaria plausível: ninguém notaria.

**A receita desconta Doação e Permuta** (`movimenta_caixa=False`), via
`apps.financeiro.services.receita`. Sem isso o mapa acenderia bairros onde não
entrou dinheiro nenhum e divergiria do faturamento do resto do ERP. O desconto
só cabe no dinheiro: em *pedidos* e *volume* ele não se aplica, porque a venda
aconteceu — o que não houve foi receita.

**Filtros:** cidade, bairro, zona, território, estado, representante, filial e
período.

**Zona (Norte/Sul/Leste/Oeste) é derivada da coordenada** — não existe campo de
zona no cadastro, e pedir preenchimento manual de centenas de clientes seria
pior que calcular do que já está geocodificado. A referência é o **centro médio
da carteira**, não a filial: uma filial na borda da cidade jogaria quase todo
mundo para um lado só.

A divisão é em **quatro cunhas, não em metades**: o cliente vai para Norte/Sul
quando se afasta mais em latitude, e para Leste/Oeste quando se afasta mais em
longitude. Com metades simples cada ponto cairia em duas zonas e a soma das
quatro daria o dobro da base — há um teste somando as quatro contra o total
justamente para travar isso.

O centro considera cidade/UF mas **não** bairro nem território: se ele se
movesse a cada filtro, "Zona Norte" mudaria de lugar conforme o recorte e dois
relatórios deixariam de ser comparáveis.

Isto é geometria, **não a divisão administrativa da prefeitura** — os limites
não batem exatamente com o que o pessoal da rua chama de Zona Norte. Para
limite exato existe o filtro de **Território**, que usa o polígono desenhado no
mapa (§11) e a atribuição já materializada em `ClienteTerritorio`. A lista só
oferece praças que têm clientes atribuídos: sem polígono não há como dizer quem
está dentro, e a opção viraria um filtro que zera tudo.

**Peso normalizado de 0 a 1** contra o maior valor do recorte. O `leaflet.heat`
satura acima de 1: mandar reais crus pintaria o mapa inteiro de vermelho. Os
absolutos voltam em `total` e `maximo`, para a legenda dizer o que a cor vale.

Dois avisos que a tela dá, e por quê:

- **Filtrar por representante deixa o balcão de fora.** `VendaPDV` não guarda
  representante; incluí-la atribuiria a um vendedor faturamento que não é dele.
  Sem o aviso, o mapa esfriaria e pareceria queda de vendas.
- **Cliente sem coordenada não entra no mapa.** O contador de quantos ficaram
  de fora evita ler um mapa incompleto como se fosse o todo.

O `filial_id` do filtro é sempre validado contra o escopo do usuário — aceitá-lo
direto deixaria qualquer um ler o faturamento de outra empresa trocando um
número na URL.

### §14 — Painel de indicadores

`/mapas/painel/` — os nove indicadores da especificação, com filtro de período
(sem parâmetros, o dia de hoje).

**Quatro deles não tinham de onde sair.** Rotas e otimizações eram calculadas,
mostradas e descartadas; sugestões de proximidade também. Sem registro, km em
rota, tempo em rota, economia da otimização e clientes sugeridos só poderiam
ser inventados. Daí os dois modelos novos (`0004_registro_rota_e_sugestao`):

| Modelo | Grava | Alimenta |
|---|---|---|
| `RegistroRota` | cada rota calculada (§4) ou otimizada (§5) | km, tempo, economia |
| `SugestaoProximidade` | cada consulta de clientes próximos (§8) | clientes sugeridos |

Os dois registros estão envoltos em `try/except`: falhar ao gravar **não pode
derrubar** a rota nem a sugestão. O log serve a um número no painel; a rota,
à operação. Há teste para isso.

**O aviso mais importante da tela:** km e tempo são de **rotas calculadas**,
não de percurso medido. Sem o rastreamento (§13, em standby) o sistema não
sabe por onde o veículo passou. Apresentar planejado como percorrido faria
alguém decidir sobre frota em cima de um número que não existe — por isso o
rótulo diz o que o número é, e um teste garante que o aviso continue na página.

Duas contagens que parecem iguais e não são:

- **Entregas** conta pedidos; **clientes visitados** conta clientes distintos
  que chegaram a `entregue`/`finalizado`. Dois pedidos para o mesmo endereço
  não são duas visitas, e pedido em preparo não visitou ninguém.
- **Rotas montadas** conta cálculos, não entregas: ajustar as paradas e
  recalcular gera várias linhas para a mesma saída.

A economia soma a diferença **de cada rota otimizada**, e não a diferença dos
totais — senão as rotas que nunca passaram pela otimização entrariam na conta.

### Modo de desenho (Leaflet.draw)

O §11 fecha: o polígono é desenhado no próprio mapa.

Fluxo: botão **“Desenhar território”** (só aparece com permissão
`mapas`/`editar`) → escolhe a praça no seletor → desenha ou ajusta → **Salvar**.
A resposta traz quantos clientes caíram no território, que é o feedback que
valida o traçado na hora.

Detalhes de implementação que importam:

- **Só a ferramenta de polígono** está habilitada. Círculo, linha e marcador
  ficariam desenháveis mas o backend os recusaria — oferecer o que não funciona
  é pior que não oferecer.
- `?todas=1` no endpoint de territórios inclui as praças **sem** polígono. São
  exatamente as que se quer desenhar pela primeira vez; o padrão continua só
  com polígono, para a camada de exibição não mudar de comportamento.
- Ao escolher uma praça que já tem polígono, ele é carregado **editável** — dá
  para arrastar vértices em vez de redesenhar do zero.
- Um território guarda **um** anel externo, sem buracos: `getLatLngs()[0]`. Um
  desenho novo substitui o anterior.
- **Remover** manda `poligono: null`, o que zera bbox e apaga as atribuições.

Cobertura: `tests/test_api_territorio.py` testa salvar, editar, remover,
polígono curto, tipo errado, JSON quebrado, método errado, permissão ausente,
usuário anônimo e — o mais importante — que **não se edita nem se lista praça
de outra empresa** (404 e lista vazia, respectivamente).

`Representante` não tem campos de endereço — só `regiao_atuacao` em texto. Para
colocá-lo no mapa (§1) é preciso decidir: endereço próprio, ou centroide dos
clientes dele.
