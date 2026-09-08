# Contexto e runbook do multibanco no Railway

> Atualizado em 08/09/2026. Este documento registra o estado deixado em
> produção, as decisões tomadas e o caminho de recuperação. Antes de qualquer
> alteração relacionada a Railway, bancos por empresa, Central Administrativa,
> domínios ou armazenamento, leia este arquivo inteiro e confirme o estado ao
> vivo. Nunca copie senhas, tokens, certificados ou URLs de banco para o Git.

Para a expansão futura com um PostgreSQL e um volume por empresa distribuídos
em vários projetos Railway, leia também
`docs/ARQUITETURA_RAILWAY_MULTIPROJETO.md`. Essa fundação é controlada pela flag
`RAILWAY_MULTI_PROJECT_ENABLED` e nasce desligada.

## Objetivo do trabalho

O objetivo foi levar ao iTed Comercial a arquitetura multibanco que existia no
Dev Edu:

- um Banco Gerencial para o diretório central;
- um PostgreSQL operacional separado para cada empresa;
- Redis para tarefas e cache;
- Central Administrativa para empresas, filiais, usuários, perfis e bancos;
- provisionamento automático de novos bancos pelo Railway;
- Sabor a Fruta usando o mesmo código, mas permanecendo isolado em outro
  projeto, banco e bucket.

O trabalho foi feito primeiro em stage e depois levado à produção. Bancos
originais não foram apagados.

## Regra de segurança principal

Nunca trate o nome de um serviço como prova de sua função atual. O serviço hoje
chamado `Sitema Ited Produção` (antes `eureka-50649395000126`) é o aplicativo
central que atende `ited.app.br`. Ele deixou de usar o banco da Eureka como
`default` e passou a usar o Banco Gerencial. A Eureka continua como tenant no
banco `Banco LR Sports`.

Antes de mudar variáveis, domínios ou bancos:

1. executar `git fetch origin main` e seguir o `AGENTS.md` da raiz;
2. confirmar projeto, ambiente e serviço por ID;
3. gerar ou validar backup recente;
4. registrar os valores lógicos atuais sem revelar segredos;
5. fazer uma mudança por vez;
6. aguardar o deployment específico chegar a `SUCCESS`;
7. testar `/health/`, login, seleção de empresa e conexões dos tenants.

## Repositório e implantações

Os aplicativos usam o mesmo repositório GitHub:

- repositório: `saborafruta/SaboraFruta`;
- branch de produção: `main`;
- a `origin/main` é sempre a fonte da verdade;
- um push em `main` pode disparar deploy no app central do iTed e no Sabor a
  Fruta.

Commits importantes deste trabalho:

- `7ceb95ba13f64fdd0ece66bcad28c26119a3ded7`: filtro de filiais pela empresa
  selecionada e consolidação do primeiro pacote multibanco;
- `f43c51e3b33041ba5fe25912efb3d38f2e4dd9cd`: recupera sessões antigas de
  Super Admin criadas antes do multibanco;
- `463d9b3f4e149ccb07496e47168ecfe1f529b90d`: faz a seleção global consultar
  filiais e empresas explicitamente no Banco Gerencial.

Esses commits devem ser ancestrais da `origin/main`. Não conclua que produção
está neles apenas pelo hash: sempre verifique o deployment mais recente, pois
o repositório recebe alterações paralelas.

## Topologia atual: iTed Comercial

Projeto Railway:

- nome: `iTed Comercial`;
- project ID: `a3fb123c-a49b-45bd-aece-8d9b75bb03d9`;
- ambiente: `production`;
- environment ID: `8a26d482-10af-4e14-abf1-1c5c2965b0c9`.

Serviços:

| Função | Nome no Railway | Service ID |
|---|---|---|
| App que atende `ited.app.br` | `Sitema Ited Produção` | `80d40f12-cc9e-4e10-a145-f4ce08b25193` |
| Worker da separação de filial | `Worker Separacao` | `f8228b97-715a-4ee7-9803-4eb7d48dcda2` |
| Banco operacional da iTed | `Banco iTed` | `1e1a0552-ac7e-4907-b8c6-e489f1f64c19` |
| Banco Gerencial | `Banco Gerencial` | `5e7f91de-3710-4c88-bb2b-6af43c324490` |
| Banco operacional da L&R Sports/Eureka | `Banco LR Sports` | `89221c0e-ad62-4e89-82fb-626ccab14a81` |
| Redis | `Redis` | `f1955f60-1129-497f-8eb5-2e0f9d427761` |

Em 07/09/2026, o app redundante `iTed Comercial` (service ID
`5d25db60-f841-4880-b156-457c89bb61de`) foi removido após validação do app
canônico. Ele não possuía volume. Nenhum banco, volume ou dado operacional foi
apagado nessa consolidação.

Empresas ativas no diretório central:

| Empresa | CNPJ | Alias Django | Filiais na migração |
|---|---|---|---:|
| iTed | `06722483000114` | `empresa_ited_06722483000114` | 3 |
| L&R SPORTS / Eureka | `50649395000126` | `empresa_eureka_50649395000126` | 1 |

Na validação final havia dois Super Admins ativos no Banco Gerencial e dois
registros `EmpresaBanco` ativos. Não fixe esses números como regra: eles podem
mudar com novos cadastros.

## Domínios

- `https://ited.app.br`: produção principal. Está anexado ao serviço
  `Sitema Ited Produção`, que foi configurado como app central;
- `https://eureka-50649395000126-production.up.railway.app`: domínio Railway do
  mesmo app que atende `ited.app.br`;
- `eureka.ited.app.br`: cadastro existe no Railway, mas o DNS estava sem
  resolução na última auditoria. Não é necessário para o fluxo central.

O domínio `https://ited.up.railway.app` pertencia ao app redundante removido e
deve responder 404. Não o use como endereço de produção.

Foi tentada a transferência de `ited.app.br` para o serviço `iTed Comercial`.
O Railway criou associação e certificado, mas o Cloudflare continuou apontando
ao alvo anterior e respondeu 404. A alteração foi revertida imediatamente, o
domínio voltou a responder 200, e o serviço já ligado ao domínio foi convertido
em app central. Não repita a transferência sem acesso ao DNS do Cloudflare e uma
janela de mudança.

## Variáveis lógicas do app central

O app central foi configurado com estas variáveis. Verifique os valores por
referência no Railway; nunca imprima segredos:

- `DATABASE_URL` -> `${{Banco Gerencial.DATABASE_URL}}`;
- `MANAGEMENT_DATABASE_URL` -> `${{Banco Gerencial.DATABASE_URL}}`;
- `TENANT_DATABASE_URL_ITED_06722483000114` -> `${{Banco iTed.DATABASE_URL}}`;
- `TENANT_DATABASE_URL_EUREKA_50649395000126` ->
  `${{Banco LR Sports.DATABASE_URL}}`;
- `TENANT_DATABASES_JSON` com os aliases da iTed e Eureka;
- `CELERY_BROKER_URL` -> `${{Redis.REDIS_URL}}`;
- `TENANT_DATABASE_ROUTING_ENABLED=True`;
- `TENANT_PUBLIC_LINK_ROUTING_READY=True`;
- `TENANT_BACKGROUND_TASKS_READY=True`;
- `TENANT_DATABASE_PROVISIONING_MODE=railway_api`;
- `RAILWAY_PROJECT_ID=a3fb123c-a49b-45bd-aece-8d9b75bb03d9`;
- `RAILWAY_ENVIRONMENT_ID=8a26d482-10af-4e14-abf1-1c5c2965b0c9`;
- `RAILWAY_PROJECT_TOKEN` configurado como segredo;
- `RAILWAY_SERVICE_ID` deve ser o ID do próprio app em cada serviço.

O token de projeto foi criado pelo usuário no projeto de produção, copiado
pela área de transferência e enviado diretamente ao Railway por `stdin`. O valor
nunca foi exibido. O token foi validado por uma consulta de leitura da API que
localizou o `Banco Gerencial`.

## Como o isolamento funciona

- O Banco Gerencial é o banco `default` dos apps centrais.
- Login, sessão, diretório de empresas, filiais, usuários, perfis,
  `EmpresaBanco` e índice de links públicos pertencem ao gerencial.
- Depois do login, usuários comuns são resolvidos pelo e-mail no diretório
  central e enviados ao alias da empresa.
- O Super Admin permanece autenticado no gerencial e escolhe qualquer empresa
  e filial ativa.
- Consultas e gravações operacionais usam o banco do tenant selecionado.
- Não existe replicação operacional contínua entre iTed e Eureka.
- O importador de diretório copia apenas empresa, filiais, perfis, permissões,
  usuários, acessos e políticas necessárias para autenticação e gestão.

### Observação sobre dados históricos no gerencial

O Banco Gerencial foi inicialmente criado a partir de um dump do antigo banco
principal da iTed para reduzir o risco da virada. Por isso, ele pode conter um
retrato histórico de tabelas operacionais da data da migração. O router impede
que o fluxo operacional multibanco continue gravando nessas tabelas centrais,
mas elas não devem ser apagadas durante a janela de rollback.

Uma limpeza futura do gerencial deve ser tratada como projeto separado,
precedida por novo backup, inventário de tabelas e autorização explícita. Nunca
execute `TRUNCATE`, `DROP`, restauração com `--clean` ou exclusão de serviço para
"organizar" o gerencial.

## Central Administrativa e seleção

A Central permite:

- listar e cadastrar empresas;
- listar e cadastrar filiais;
- gerenciar bancos das empresas;
- gerenciar usuários, acessos, perfis e permissões;
- configurar segmento/vertical por empresa;
- provisionar, testar, migrar e ativar bancos dedicados.

Os módulos operacionais aparecem somente depois de selecionar empresa e filial.
Ao escolher uma empresa, a lista de filiais deve mostrar somente filiais dela.
O Super Admin, ao voltar para a tela global, deve ver todas as empresas e
filiais do Banco Gerencial.

Foi corrigido um problema de sessão antiga: sessões criadas antes da ativação
do multibanco não tinham `auth_database_alias=default` e eram confundidas com o
usuário local da Eureka. Também foi corrigida a consulta global para usar
explicitamente `.using('default')` enquanto o contexto operacional ainda aponta
ao tenant anterior.

Teste de regressão em produção simulou uma sessão antiga dentro da Eureka e
obteve:

- banco de autenticação: `default`;
- 4 filiais;
- 2 empresas.

### Correção do contexto ERK nas telas de gestão (07/09/2026)

Foi identificado em produção que, após selecionar a ERK/L&R Sports, abrir
`/gestao/usuarios/` mostrava a filial `MATRIZ` e usuários da iTed. Os bancos não
misturaram dados: a rota continuava corretamente no Banco Gerencial, mas usava
diretamente o PK da filial salvo no banco ERK. Como bancos independentes podem
reutilizar o mesmo PK, o número da filial ERK coincidia com a MATRIZ no
gerencial.

A resolução passou a preservar o alias do tenant selecionado nas rotas
centrais e traduzir a filial por três chaves: projeto lógico (`EmpresaBanco`),
empresa e CNPJ da filial. Nunca se usa apenas o PK entre bancos. Se a
correspondência não puder ser comprovada, a sessão volta para a seleção de
empresa/filial em vez de abrir dados de outra empresa. A regressão cobre
Usuários, Perfis e o caso explícito de PK coincidente.

Na auditoria que encontrou o problema:

- o deployment estava em `SUCCESS`;
- 1.966 requisições em seis horas tiveram zero respostas 5xx;
- todos os três bancos estavam sem migrations pendentes;
- as conexões iTed e Eureka foram validadas;
- as 82 referências de arquivos da Eureka foram verificadas e nenhuma estava
  ausente (42 arquivos de pedidos, 37 imagens visuais, logo e duas fotos).

## Provisionamento automático de nova empresa

O modo `railway_api` está ativo. O fluxo esperado é:

1. cadastrar empresa e filial no Banco Gerencial;
2. criar ou obter o registro `EmpresaBanco`;
3. solicitar provisionamento;
4. criar serviço PostgreSQL e volume no mesmo projeto/ambiente;
5. gravar a variável `TENANT_DATABASE_URL_<EMPRESA>` no app canônico;
6. aguardar o PostgreSQL responder;
7. executar migrations no alias;
8. sincronizar somente o diretório necessário;
9. ativar o banco;
10. reconstruir/validar links públicos quando aplicável.

O token e a API foram validados sem criar um banco descartável, evitando custo.
Ao provisionar a primeira empresa real nova, acompanhe o processo até o banco
ficar `ATIVO` e confirme um login de usuário comum.

### Aplicativo central consolidado

Existe apenas um app central no projeto: o serviço `Sitema Ited Produção`, que
atende `ited.app.br`. O provisionador grava a variável do novo tenant nesse
serviço, identificado por `RAILWAY_SERVICE_ID`. Depois de criar um tenant,
valide o banco, o login e a seleção de empresa pelo domínio canônico.

## Sabor a Fruta permanece separado

Projeto Railway:

- nome: `SaboraFruta`;
- project ID: `32ec3314-c815-4c24-a63c-0f3bb249a84e`;
- environment ID: `d8db136f-064d-4125-bc0f-3708ac9cf7c7`;
- app service ID: `6d6dc6a9-f00b-48b5-b760-5454cbe94352`;
- PostgreSQL service ID: `b106f4fd-2e6b-4db3-a649-cf0e42136009`;
- bucket exclusivo: `saborafruta-media`;
- bucket ID: `39fe2d63-9c56-4b63-babc-66e46fb4967e`;
- URL: `https://saborafruta-production.up.railway.app`.

Mesmo usando o mesmo repositório, o Sabor a Fruta não é um tenant do
`ited.app.br`. Não a cadastre no Banco Gerencial e não aponte `DATABASE_URL`,
Redis ou bucket dela para recursos do projeto iTed sem nova decisão explícita.

## Arquivos, imagens, PDFs e certificados

### iTed e Eureka

iTed e Eureka usavam o mesmo bucket S3 durante a migração. Foi feito backup
fora da Railway:

- pasta: `media-ited-eureka-s3` dentro do diretório de backup;
- 436 objetos;
- 375.551.393 bytes;
- manifesto com SHA-256 por objeto;
- contagem e tamanho permaneceram estáveis antes e depois da cópia.

### Sabor a Fruta

O volume `/app/media` estava vazio. O banco continha 8 referências de arquivos
antigas, mas nenhuma delas estava acessível antes da migração. Uma imagem ainda
existia no bucket histórico compartilhado e foi recuperada para o bucket
exclusivo `saborafruta-media`.

Resultado:

- 1 objeto recuperado, com 25.796 bytes;
- 7 referências continuaram ausentes porque os arquivos já não existiam antes
  do trabalho;
- 2 certificados armazenados em campo `certificado_base64` permaneceram nos
  dumps dos bancos;
- não houve nova perda de arquivo causada pela migração.

## Backups locais de rollback

Diretório fora do repositório:

`C:\Users\Windows 10\Documents\Railway-Backups\pre-multibanco-2026-09-07`

Arquivos principais:

| Arquivo | Tamanho validado |
|---|---:|
| `ited-principal.dump` | 2.838.611 bytes |
| `eureka.dump` | 3.225.616 bytes |
| `saborafruta.dump` | 3.728.426 bytes |

Os dumps foram gerados por `pg_dump` no ambiente Railway, confirmados com
cabeçalho `PGDMP` e lidos por `pg_restore --list`. O `README.md` do diretório
de backup registra hashes e detalhes adicionais. Os dumps e o backup de media
não devem ser adicionados ao Git.

As tentativas de snapshot nativo do Railway falharam por escopo OAuth
insuficiente (`OAUTH_INSUFFICIENT_GRANT`). O fallback usado foi dump lógico
local validado.

## Plano de rollback

O rollback mais seguro depende do tipo de problema.

### Falha de código

1. identificar o último deployment comprovadamente saudável;
2. reverter o commit problemático na `main`, sem `push --force`;
3. acompanhar os apps afetados até `SUCCESS`;
4. validar `ited.app.br/health/`, login e seleção de filial.

### Falha no roteamento multibanco

Antes de alterar qualquer variável, confirmar se houve gravações recentes em
mais de um tenant. Um rollback de configuração pode recolocar temporariamente o
app no banco antigo, mas não deve juntar dados produzidos separadamente.

Ordem geral:

1. registrar o estado e gerar novos dumps dos bancos atuais;
2. desligar `TENANT_DATABASE_ROUTING_ENABLED` no app afetado;
3. restaurar `DATABASE_URL` para o banco original correspondente somente se a
   análise confirmar que isso não esconderá dados novos;
4. redeployar e aguardar `SUCCESS`;
5. validar login, filiais, estoque, financeiro, fiscal e arquivos;
6. manter Banco Gerencial e tenants intactos até conciliação completa.

Os bancos originais continuam preservados, agora renomeados para `Banco iTed` e
`Banco LR Sports`. O app redundante foi removido porque não tinha volume nem
dados próprios. O rollback de configuração continua possível apontando o app
canônico aos bancos preservados, mas isso não autoriza apagar ou sobrescrever
dados criados após a virada.

### Corrupção ou perda de banco

1. parar novas gravações no banco afetado;
2. criar um PostgreSQL novo;
3. restaurar o dump mais recente no banco novo;
4. aplicar migrations compatíveis;
5. comparar empresas, filiais, usuários e contagens críticas;
6. apontar a variável do tenant ao banco restaurado;
7. redeployar e testar antes de liberar usuários.

Nunca restaure um dump por cima de um banco atual sem novo backup e autorização
explícita.

## Comandos de diagnóstico seguros

Use os IDs acima em vez de depender do projeto ligado no diretório local.

```powershell
railway deployment list `
  --project a3fb123c-a49b-45bd-aece-8d9b75bb03d9 `
  --environment 8a26d482-10af-4e14-abf1-1c5c2965b0c9 `
  --service "Sitema Ited Produção" --json

railway logs `
  --project a3fb123c-a49b-45bd-aece-8d9b75bb03d9 `
  --environment 8a26d482-10af-4e14-abf1-1c5c2965b0c9 `
  --service "Sitema Ited Produção" --lines 200 --json

railway ssh `
  --project a3fb123c-a49b-45bd-aece-8d9b75bb03d9 `
  --environment 8a26d482-10af-4e14-abf1-1c5c2965b0c9 `
  --service "Sitema Ited Produção" `
  python manage.py check_tenant_databases
```

Health checks:

```powershell
curl.exe https://ited.app.br/health/
curl.exe https://saborafruta-production.up.railway.app/health/
```

## Validações já realizadas

- `python manage.py check` sem erros;
- testes críticos do multibanco no PostgreSQL de stage: 23 testes aprovados;
- testes locais de regressão da sessão e seleção global: 18 testes
  aprovados no pacote `test_multitenancy`;
- `git diff --check` aprovado;
- conexão validada para iTed e Eureka;
- aliases resolvendo o banco correto;
- hash de senha do diretório compatível com o usuário do tenant na
  verificação de migração;
- 35 links públicos indexados na migração: 1 da iTed e 34 da Eureka;
- endpoints principais respondendo HTTP 200;
- token de projeto e leitura da API Railway validados;
- app canônico do iTed, seus três bancos e o Redis em `SUCCESS` após a
  consolidação de 07/09/2026;
- `ited.app.br/health/` respondeu HTTP 200 com banco e `media_root` válidos após
  a remoção do app redundante;
- os aliases da iTed e da L&R Sports foram novamente validados após a
  renomeação dos serviços de banco.
- em 08/09/2026, o `Worker Separacao` entrou em `SUCCESS`, conectou ao Redis,
  registrou `apps.core.tasks.executar_separacao_filial`, carregou as duas URLs
  atuais de tenant pelo app de controle e confirmou `S3Storage` no bucket;
- o app iTed aplicou `core.0062_separacao_filial_segura`, validou as migrations
  dos tenants iTed/Eureka e respondeu 200 em `/health/`; o Sabor a Fruta também
  aplicou a migration e respondeu 200, permanecendo em infraestrutura separada.

## Central visual de infraestrutura (08/09/2026)

A administração cotidiana não deve mais depender das telas cruas do Django
Admin. A Central Administrativa passou a organizar quatro áreas próprias:

- **Listar empresas**: cadastro, edição, estado e acesso às filiais;
- **Listar filiais**: filtro obrigatório por empresa quando desejado, cadastro
  e edição das unidades;
- **Bancos das empresas**: visão de cada empresa, número de filiais, alias,
  serviço PostgreSQL, projeto Railway, estado e última verificação, além do
  detalhe do banco sem exibir senha nem URL de conexão;
- **Gestão Railway**: novo nome da antiga tela “Projetos de bancos”, reservada
  à capacidade dos projetos, modo de conexão, Project Token por variável e
  validação/ativação. Cadastro e edição também usam telas próprias.

Rotas principais:

- `/gestao/empresas/bancos/`;
- `/gestao/central/gestao-railway/`;
- `/gestao/empresas/`;
- `/gestao/filiais/`.

A URL antiga `/gestao/central/projetos-bancos/` apenas redireciona para Gestão
Railway para preservar favoritos. O Django Admin continua disponível como
ferramenta de emergência técnica, mas não é o fluxo normal da Central.

## Portabilidade das telas do Dev Edu (08/09/2026)

- Referência confirmada em produção: projeto Railway **Dev Edu**, serviço
  **iTed**, repositório `epalhetta/ited`, commit
  `5c04503ebfde1ded819140a2f97650c99d9fbaeb`.
- As telas de **Empresas**, **Filiais** e **Bancos das empresas** foram portadas
  desse commit para manter o visual, filtros, paginação, modais de consulta e
  menus compactos usados em `ited2.up.railway.app`.
- A URL canônica dos bancos é `/gestao/empresas/bancos/`. A URL anterior
  `/gestao/central/bancos-empresas/` redireciona para ela.
- O backend mais novo de `RailwayProjectPool` e o provisionamento multiprojeto
  foram preservados. Não substituir esses serviços pelos arquivos antigos do
  Dev Edu, pois isso removeria a seleção automática de projetos e o TCP Proxy
  usado pelos bancos hospedados fora do projeto principal.
- A operação **Separar filial** foi adaptada ao modelo atual, incluindo banco
  multiprojeto, simulação, bloqueio por operações abertas, confirmação dupla,
  execução Celery, acompanhamento de progresso, backups e relatório final.
- Ações de banco agora incluem configuração acompanhada por progresso, backup
  completo em ZIP (SQL, CSVs e manifesto) e exclusão protegida. A exclusão só
  prossegue depois do backup, da razão social completa e da senha master (ou
  senha do Super Admin quando a variável master estiver vazia).
- Bancos importados manualmente também podem ter o serviço Railway removido
  quando possuem `railway_database_service_id`. Sem identificação inequívoca
  do serviço físico, a operação é bloqueada para não apagar somente o cadastro
  central e deixar infraestrutura órfã.

## Separação segura de filial (08/09/2026)

O fluxo portado do Dev Edu foi mantido na Central, mas adaptado à arquitetura
mais nova deste repositório:

1. **Separar filial** abre uma simulação sem alterar dados;
2. o sistema bloqueia empresa com uma única filial ativa, CNPJ inválido ou
   duplicado e operações ainda abertas;
3. o administrador precisa digitar `SEPARAR` e confirmar a senha;
4. um worker Celery executa a operação fora da requisição web;
5. antes da mudança administrativa são gerados backup de restauração e
   exportação auditável;
6. a filial vira uma empresa independente, recebe perfis próprios e banco
   dedicado, e seus dados operacionais são copiados para esse banco;
7. usuários exclusivos acompanham a unidade; usuários compartilhados ficam
   sinalizados para revisão;
8. a replicação é desligada e não pode ser reativada automaticamente depois da
   separação, evitando reconciliar bancos independentes sem análise;
9. a tela de progresso pode ser reaberta e os arquivos de segurança continuam
   disponíveis para download enquanto permanecerem no armazenamento.

O modelo `core.separacaofilial` e `core.filialfavorita` pertencem ao Banco
Gerencial (`TENANT_GLOBAL_MODELS`). Nunca execute o worker sem as mesmas
variáveis de banco/tenant e Redis do app central. Em produção,
`SEPARACAO_FILIAL_ASYNC_MODE=celery`; `inline` serve somente para testes locais.
No worker, `RAILWAY_CONTROL_SERVICE_ID` deve apontar para o app web canônico,
não para o próprio worker. Antes de cada tarefa, ele consulta esse app e carrega
as URLs atuais dos tenants, inclusive bancos criados depois do último deploy.

Os pacotes de backup não ficam no disco efêmero do container: são enviados ao
`default_storage`. No iTed isso significa o bucket `media`, compartilhado pelo
app web e pelo worker. Os campos `backup_path` guardam a chave do objeto, e o
download da Central abre o arquivo pelo storage. Não troque isso por um caminho
local em produção.

Não existe separação silenciosa: nenhum botão inicia a operação final antes da
simulação e da confirmação. Ainda assim, antes de usar em empresa real,
confirme que o worker está `SUCCESS`, que o bucket/volume de backups responde e
que o projeto Railway escolhido possui vaga operacional.

## Pendências e melhorias futuras

Estas pendências não bloqueiam o uso atual de `ited.app.br`, mas devem ser
tratadas conscientemente:

1. corrigir o DNS de `eureka.ited.app.br` se esse subdomínio ainda for desejado;
2. acompanhar ponta a ponta o primeiro banco criado para uma empresa real;
3. definir retenção e automação de backups fora da Railway;
4. testar restauração completa periodicamente;
5. avaliar limpeza das tabelas operacionais históricas do Banco Gerencial
   somente depois da janela de segurança;
6. avaliar a normalização de antigos superusuários nos bancos tenants apenas
   depois de garantir que o rollback para o modo antigo não será mais usado.

## Checklist para a próxima IA

Antes de agir:

- [ ] ler o `AGENTS.md` da raiz e este documento inteiro;
- [ ] buscar a `origin/main` e usar worktree limpo se necessário;
- [ ] confirmar qual serviço atende `ited.app.br` naquele momento;
- [ ] confirmar `DATABASE_URL` logicamente, sem revelar seu valor;
- [ ] confirmar flags de roteamento e provisionamento;
- [ ] confirmar os registros `EmpresaBanco` ativos;
- [ ] confirmar conexão dos aliases com `check_tenant_databases`;
- [ ] confirmar que Sabor a Fruta continua em projeto, banco e bucket separados;
- [ ] confirmar backups antes de qualquer operação destrutiva;
- [ ] nunca apagar os bancos e serviços antigos sem autorização explícita;
- [ ] depois de qualquer deploy, aguardar o deployment exato chegar a
  `SUCCESS` e testar os endpoints.

