# ERP iNoovaTed — Instruções para Claude

## Regra de Deploy (IMPORTANTE)

**Sempre que fizer `git push`, enviar para AMBAS as branches:**

```bash
git push origin main
git push origin thiago/dashboard
```

O Railway pode estar configurado para fazer deploy de qualquer uma dessas branches. Enviar para as duas garante que as atualizações cheguem ao ambiente de produção.

## Branches

- `main` — branch principal
- `thiago/dashboard` — branch de deploy (Railway pode usar esta)

## Stack

- Django (Python) + Alpine.js + Tailwind CSS
- PostgreSQL (via Railway)
- Deploy: Railway (Docker)

## Railway Deploy

O Railway faz **deploy automático a cada push** para o GitHub — na prática
é o push que publica, não a chamada de API.

Serviço que atende `saborafruta-production.up.railway.app`:

- **Service ID:** `6d6dc6a9-f00b-48b5-b760-5454cbe94352`
- **Environment ID:** `d8db136f-064d-4125-bc0f-3708ac9cf7c7`
- **Token:** header `Project-Access-Token: 6d69fa40-5841-462a-89e1-031fe4798b6f`

> Os IDs antigos (`a49870de…` / `168e0342…` com `Authorization: Bearer c1c560e5…`)
> apontavam para outro serviço, desativado desde junho/2026. A mutation
> respondia `true` sem efeito nenhum sobre produção.

Forçar um redeploy (raramente necessário — o push já publica):

```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Project-Access-Token: 6d69fa40-5841-462a-89e1-031fe4798b6f" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "mutation serviceInstanceRedeploy($environmentId: String!, $serviceId: String!) { serviceInstanceRedeploy(environmentId: $environmentId, serviceId: $serviceId) }",
    "variables": {
      "environmentId": "d8db136f-064d-4125-bc0f-3708ac9cf7c7",
      "serviceId": "6d6dc6a9-f00b-48b5-b760-5454cbe94352"
    }
  }'
```

### Conferir se o deploy subiu

`railway.toml` roda `migrate --noinput` como release command: **se uma
migration falhar, o container não sobe e o site fica em 502**. Vale
checar o status e, em caso de falha, ler os logs:

```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H "Project-Access-Token: 6d69fa40-5841-462a-89e1-031fe4798b6f" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { deployments(first: 1, input: {serviceId: \"6d6dc6a9-f00b-48b5-b760-5454cbe94352\"}) { edges { node { id status } } } }"}'
```

Com o `id` de um deployment `CRASHED`, os logs saem em
`deploymentLogs(deploymentId: "…", limit: 100) { message }`.

## Migrations

Antes de criar uma migration, confira o último número **no branch já
atualizado** (`git fetch` primeiro). Duas migrations com o mesmo número
no mesmo app viram dois leaf nodes e o Django recusa migrar
("Conflicting migrations detected"), derrubando o deploy — o rebase não
avisa, porque são arquivos diferentes.
