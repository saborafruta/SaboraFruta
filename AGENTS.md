# Instrucoes obrigatorias para agentes de IA

## Nao sobrescrever trabalho recente

Antes de alterar, commitar ou subir qualquer arquivo, o agente deve sincronizar
com o GitHub e conferir se esta partindo da versao mais nova.

Fluxo obrigatorio antes de qualquer commit/push:

```bash
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git status --short
git diff
```

Se `origin/main` tiver commits que nao estao no checkout local, nao faca push.
Primeiro atualize com `git pull --rebase origin main` ou crie um worktree limpo
baseado em `origin/main`.

Nunca faca push contendo arquivo antigo por cima de arquivo mais recente do
GitHub. Se aparecer alteracao em arquivo que voce nao alterou intencionalmente,
pare e investigue antes de continuar.

Regra pratica: cada linha no `git diff` precisa ser uma mudanca consciente da
tarefa atual. Se nao for, nao suba.

## Proibido

- `git push --force` ou `git push --force-with-lease` sem autorizacao explicita.
- Commitar arquivos gerados, temporarios ou alteracoes fora do escopo.
- Resolver conflito escolhendo tudo de um lado sem entender o que esta sendo
  perdido.
- Subir branch local antiga direto para `main`.

## Validacao minima

Antes do push, rode pelo menos:

```bash
python manage.py check
git diff --check
```

Quando alterar uma area especifica, rode tambem os testes daquela area.
