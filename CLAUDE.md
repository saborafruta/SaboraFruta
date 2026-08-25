# ERP iNoovaTed — Instruções para Claude

## Regra anti-regressao: nunca subir estado antigo

Antes de qualquer alteracao, commit ou push, sincronize com o GitHub e confirme
que voce esta trabalhando em cima da versao mais recente:

```bash
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git status --short
git diff
```

Se `origin/main` tiver commits que nao estao no checkout local, nao faca push.
Atualize com `git pull --rebase origin main` ou use um worktree limpo baseado em
`origin/main`.

Nao sobrescreva arquivos com versoes antigas. Se o diff mostrar mudanca em
arquivo que voce nao mexeu conscientemente, pare e investigue. Cada linha
commitada precisa pertencer a tarefa atual.

Proibido usar `git push --force` ou `git push --force-with-lease` sem
autorizacao explicita.

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

> ## ⚠️ O serviço abaixo NÃO é a produção que o usuário usa
>
> O sistema real é **`ited.app.br`**, e ele está em **outro projeto
> Railway** — não neste. Verificado em 22/08/2026 pela API:
>
> - os domínios deste serviço são só `saborafruta-production.up.railway.app`
>   (`customDomains` vazio — `ited.app.br` não está aqui);
> - o projeto `32ec3314…` tem **um único** ambiente (`production`) e um
>   único app, então também não é caso de ambiente separado;
> - o Postgres deste serviço está **vazio**: `moda_tamanhos`, `moda_grades`,
>   `moda_itens_grade`, `moda_pedidos`, `moda_itens_pedido` e
>   `moda_grade_pedido` todos com 0 linhas.
>
> Os dois deploys puxam do MESMO repositório GitHub. Por isso mudança de
> **código** aparece em `ited.app.br` normalmente (conferido: o CSS servido
> nos dois domínios tem sha256 idêntico), e é fácil concluir errado que
> este serviço é a produção.
>
> A diferença aparece em **dado**: migration de dado, seed ou qualquer
> consulta ao banco daqui fala com a base vazia, não com a do usuário.
> Um `SELECT` que volta 0 aqui não significa que a tabela do usuário está
> vazia — significa que se está olhando o banco errado.
>
> Conferir deploy pela API deste serviço também só diz que ESTE subiu.
> Para confirmar que algo chegou em `ited.app.br`, olhe o próprio
> `ited.app.br`.
>
> **Falta descobrir**: o projeto/serviço Railway de `ited.app.br` e um token
> que o alcance. Sem isso não dá para inspecionar nem migrar o banco real.

Serviço que atende `saborafruta-production.up.railway.app` (o vazio, veja
o aviso acima):

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
