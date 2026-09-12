# API de Integrações iTed — v1

Base de integração para sistemas externos, incluindo emissores de etiquetas,
aplicativos industriais, leitores, coletores e automações.

## Segurança e isolamento

- URL base de produção: `https://ited.app.br/api/v1/`
- Cada credencial pertence a uma única empresa.
- O servidor escolhe o banco da empresa pela credencial; o consumidor nunca
  informa um banco ou empresa na requisição.
- A chave é armazenada somente como SHA-256. O valor original aparece uma vez,
  na criação.
- Uma chave pode ter escopos, data de expiração, restrição de filiais e lista
  de IPs/CIDRs permitidos.
- A API começa somente com leitura. Operações de escrita devem nascer em um
  escopo separado e com idempotência.

Envie a chave em um destes formatos:

```http
X-API-Key: ited_PREFIXO.SEGREDO
```

ou:

```http
Authorization: ApiKey ited_PREFIXO.SEGREDO
```

Nunca coloque a chave em query string ou em código público.

## Recursos iniciais

| Método e rota | Escopo | Finalidade |
|---|---|---|
| `GET /api/v1/` | autenticado | Descoberta da API e links disponíveis |
| `GET /api/v1/contexto/` | autenticado | Empresa, filiais e escopos da chave |
| `GET /api/v1/filiais/` | `filiais:ler` | Unidades permitidas |
| `GET /api/v1/clientes/` | `clientes:ler` | Clientes, contatos e endereços |
| `GET /api/v1/clientes/{id}/` | `clientes:ler` | Cliente específico |
| `GET /api/v1/produtos/` | `produtos:ler` | Produtos gerais do ERP |
| `GET /api/v1/produtos/{id}/` | `produtos:ler` | Produto geral específico |
| `GET /api/v1/estoque/` | `estoque:ler` | Saldos por produto, filial e depósito |
| `GET /api/v1/moda/produtos/` | `moda:ler` | Modelos de confecção |
| `GET /api/v1/moda/produtos/{id}/` | `moda:ler` | Modelo de confecção específico |
| `GET /api/v1/moda/variantes/` | `moda:ler` | SKU, código de barras, cor e tamanho |
| `GET /api/v1/moda/ordens-producao/` | `ops:ler` | Ordens para produção e etiquetas |
| `GET /api/v1/moda/ordens-producao/{id}/` | `ops:ler` | OP, grade e personalizações individuais |

As listas usam `pagina` e `por_pagina` (máximo 200). Também aceitam
`filial_cnpj` quando o recurso é diretamente filializado. Clientes, produtos,
estoque, produtos de moda e OPs aceitam `busca` e
`atualizado_desde` em ISO 8601. Isso permite sincronização incremental sem
baixar toda a base em cada execução.

Produtos gerais usam `ativo=true` por padrão. Produtos e variantes de moda
aceitam `ativo=true` ou `ativo=false`; sem esse filtro, retornam ativos e
inativos para permitir que integrações sincronizem desativações.

Exemplos:

```bash
curl -H "X-API-Key: SUA_CHAVE" \
  "https://ited.app.br/api/v1/moda/variantes/?busca=CAM-M-AZUL"

curl -H "X-API-Key: SUA_CHAVE" \
  "https://ited.app.br/api/v1/moda/ordens-producao/?status=liberada&por_pagina=100"

curl -H "X-API-Key: SUA_CHAVE" \
  "https://ited.app.br/api/v1/produtos/?atualizado_desde=2026-09-12T10:30:00Z"
```

Para etiquetas de confecção, a combinação recomendada é consultar as variantes
para etiquetas de catálogo e o detalhe da OP para etiquetas personalizadas por
peça. Valores monetários são strings com duas casas decimais para evitar perda
de precisão em JSON.

### Dados para etiquetas comerciais

`GET /api/v1/produtos/` entrega, entre outros campos:

- descrição completa e nomes curtos para PDV;
- código interno, código de barras principal e códigos extras;
- marca, categoria, subcategoria e unidade;
- preço normal, promoção cadastrada e `preco_atual`, já calculado pelas regras
  comerciais vigentes no dia da consulta;
- origem do preço atual, vigência da promoção, moeda e dados de balança;
- imagem do produto, logotipo da filial, pesos e dimensões.

Use `preco_atual` na etiqueta de preço ou gôndola. `preco_venda` representa o
preço normal e não deve substituir `preco_atual` quando o objetivo for imprimir
o valor comercial vigente.

A API é independente de impressora. O sistema consumidor usa os mesmos dados
para gerar o comando aceito pelo driver ou linguagem configurada da Zebra,
Elgin L45 Pro Full ou Argox. Assim, trocar a impressora não altera o contrato
com o iTed.

### Modos de integração

1. **Consulta manual:** ao pesquisar, selecionar ou solicitar a impressão, o
   sistema consulta o produto, estoque, variante ou OP naquele momento.
2. **Sincronização automática:** o sistema executa consultas periódicas usando
   `atualizado_desde`, guarda uma cópia local e oferece uma atualização manual
   adicional. A recomendação inicial é sincronizar a cada 5 minutos.

Esses dois modos podem coexistir. A sincronização reduz o tempo de resposta e a
consulta manual permite conferir um dado imediatamente antes da impressão.
Atualização realmente instantânea, sem consulta periódica, exige webhooks; esse
é o próximo mecanismo previsto para a API, não deve ser simulado como “tempo
real”.

## Códigos de resposta

- `200`: consulta concluída;
- `400`: filtro inválido;
- `401`: chave ausente, inválida, expirada, revogada ou origem não autorizada;
- `403`: chave válida sem o escopo solicitado;
- `404`: registro inexistente ou fora das filiais permitidas;
- `429`: limite de requisições atingido;
- `500/503`: indisponibilidade interna ou do banco da empresa.

## Administração

Criação de chave (executar no ambiente da aplicação):

```bash
python manage.py criar_chave_integracao \
  --empresa-cnpj 06722483000114 \
  --nome "Sistema de etiquetas" \
  --escopos filiais:ler,clientes:ler,produtos:ler,estoque:ler,moda:ler,ops:ler
```

Para restringir, repita `--filial-cnpj`. Revogação, expiração, escopos e IPs
podem ser administrados no Django Admin em **Integrações por API**. Rotacionar
uma chave significa criar outra, trocar no consumidor e revogar a anterior.

## Evolução prevista

Novos recursos devem ser adicionados sob `/api/v1/`, preservando o contrato.
Escritas futuras devem exigir escopos como `pedidos:escrever`, aceitar uma chave
de idempotência e registrar auditoria. Webhooks devem ser assinados e possuir
tentativas com backoff, sem reutilizar a chave de consulta como assinatura.
