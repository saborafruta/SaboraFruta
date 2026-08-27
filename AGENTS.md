# Instruções obrigatórias para agentes de IA

Este arquivo fica na raiz do repositório para ser lido automaticamente por
agentes de IA que trabalham neste projeto. Estas regras valem desde o início de
cada nova conversa ou tarefa.

## Fonte da verdade: GitHub

A `origin/main` do GitHub é a fonte da verdade para todo trabalho novo. Nunca
comece uma alteração usando apenas o estado que já estava aberto no computador.

Antes de ler código para implementar uma tarefa, editar arquivos ou propor uma
correção, execute:

```bash
git fetch origin main
git status --short
git rev-list --left-right --count HEAD...origin/main
```

Depois aplique obrigatoriamente uma destas opções:

- Se o diretório principal estiver limpo e apenas atrás da `origin/main`,
  atualize-o com `git pull --ff-only origin main`.
- Se houver alterações locais, divergência ou outro trabalho em andamento, não
  edite esse checkout antigo. Crie um worktree limpo e uma branch nova a partir
  da `origin/main` e faça a tarefa lá.
- Nunca crie uma branch de tarefa a partir de uma branch local antiga.
- Nunca descarte alterações locais sem autorização explícita do usuário. A
  existência de alterações locais não justifica trabalhar sobre código antigo.

Exemplo de worktree seguro:

```bash
git worktree add -b codex/nome-da-tarefa ../Saborafruta-nome-da-tarefa origin/main
```

## Antes de commit e push

Sincronize novamente, pois a `main` pode ter avançado durante a tarefa:

```bash
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git status --short
git diff
```

Se a `origin/main` tiver commits novos, integre-os antes do push, normalmente
com `git rebase origin/main`, e repita as validações relevantes.

Nunca faça push contendo arquivo antigo por cima de arquivo mais recente do
GitHub. Cada linha do `git diff` deve ser uma mudança consciente da tarefa
atual. Se aparecer alteração não intencional, pare e investigue.

Ao concluir e publicar uma tarefa feita em worktree, confirme que o commit está
na `origin/main`. Se o diretório principal estiver limpo, atualize-o também com
`git pull --ff-only origin main`, para que a próxima conversa encontre a versão
mais recente.

## Proibido

- Usar `git push --force` ou `git push --force-with-lease` sem autorização
  explícita.
- Commitar arquivos gerados, temporários ou alterações fora do escopo.
- Resolver conflitos escolhendo tudo de um lado sem entender o que será perdido.
- Subir uma branch local antiga diretamente para a `main`.
- Reutilizar um worktree antigo como base de uma tarefa nova sem antes confirmar
  que ele contém a `origin/main` atual.

## Validação mínima

Antes do push, execute pelo menos:

```bash
python manage.py check
git diff --check
```

Ao alterar uma área específica, rode também os testes correspondentes. Depois de
um deploy, acompanhe o serviço até um estado terminal de sucesso antes de dizer
ao usuário que a publicação terminou.

## Eficiência

Trabalhar com arquivos locais não é, por si só, mais lento nem consome mais
tokens. Em geral, leitura, busca e testes locais são o caminho mais rápido. Para
manter eficiência:

- atualize primeiro apenas as referências Git necessárias;
- use `rg` e diffs direcionados em vez de ler o repositório inteiro;
- trabalhe sempre sobre a versão mais recente da `origin/main`;
- preserve alterações alheias usando worktrees limpos quando necessário.
