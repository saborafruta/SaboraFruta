# Instrucoes para assistentes de IA neste repositorio

Antes de editar ou subir codigo, sempre atualize sua visao do GitHub:

```bash
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git status --short
git diff
```

Nao sobrescreva trabalho recente com arquivos antigos. Se `origin/main` tiver
commits que nao existem no checkout local, faca rebase/pull antes de commitar ou
use um worktree limpo baseado em `origin/main`.

Nao de push se o diff contiver arquivo que voce nao alterou de proposito. Cada
mudanca precisa ser intencional, revisada e testada.

Nunca use force push sem autorizacao explicita.
