# Arquitetura Railway multiprojeto para bancos dedicados

> Estado em 07/09/2026: fundação implementada no código, com ativação protegida
> pela variável `RAILWAY_MULTI_PROJECT_ENABLED`. Nenhum projeto, banco, volume,
> TCP Proxy ou credencial de produção foi criado por esta implementação local.
> Leia também `docs/CONTEXTO_MULTIBANCO_RAILWAY.md` antes de operar o Railway.

## Objetivo e decisão arquitetural

O `ited.app.br` continuará sendo a única aplicação central. Cada empresa terá
um serviço PostgreSQL e um volume exclusivos. Não serão usados shards e bancos
de empresas diferentes não compartilharão um PostgreSQL.

Quando o projeto Railway atual se aproximar do limite de volumes, bancos de
novas empresas poderão ser criados em projetos adicionais. A aplicação
continuará no projeto principal e acessará os bancos externos por TCP Proxy com
SSL obrigatório, pois a rede privada do Railway é isolada por projeto e por
ambiente.

O Sabor a Fruta continua fora dessa arquitetura: projeto, banco e bucket
separados, embora compartilhe o repositório de código.

## Componentes implementados

### `RailwayProjectPool`

Registro mantido no Banco Gerencial com:

- nome operacional do projeto;
- Project ID e Environment ID;
- nome da variável protegida que contém o Project Token;
- modo de conexão `private` ou `public`;
- prioridade de alocação;
- limite de volumes e reserva de emergência;
- último total observado, data de verificação e erro;
- estado `ativo`, `lotado`, `manutencao` ou `erro`.

O token não é armazenado no banco. `token_env_var` guarda apenas o nome da
variável de ambiente do aplicativo central.

Novos registros nascem inativos e em manutenção. A ação **Validar e ativar**
consulta o projeto com o token informado, conta volumes e só ativa o pool se
houver capacidade operacional.

### Vinculação da empresa

`EmpresaBanco.railway_project_pool` registra onde está o PostgreSQL dedicado.
Também são guardados apenas metadados não secretos do volume e TCP Proxy:

- service ID e nome do banco;
- volume ID;
- TCP Proxy ID, domínio e porta.

A URL completa e a senha do banco permanecem nas variáveis do serviço no
Railway, nunca no Banco Gerencial e nunca no Git. Membros autorizados do projeto
Railway podem administrar essas variáveis, portanto o acesso ao projeto deve ser
restrito.

### Seleção por capacidade

Ao provisionar uma nova empresa com a feature flag ligada:

1. reutilizar o pool já associado, se existir;
2. bloquear transacionalmente a empresa e os pools candidatos;
3. ordenar pools ativos por prioridade;
4. comparar a capacidade operacional com o maior valor entre volumes
   observados e bancos já associados;
5. reservar o pool na empresa antes de criar recursos externos;
6. falhar de forma explícita se não houver vaga.

A reserva permanece após uma falha externa. Dessa forma, uma repetição procura
o mesmo serviço pelo ID ou pelo nome e não cria outro banco em outro projeto.

### Provisionamento remoto

Para um pool público, o fluxo é:

1. localizar ou criar o serviço PostgreSQL;
2. localizar ou criar um volume exclusivo;
3. localizar ou criar um TCP Proxy para a porta 5432;
4. fazer deploy do serviço quando necessário;
5. montar uma URL pública com credenciais escapadas e `sslmode=require`;
6. gravar a variável `TENANT_DATABASE_URL_<EMPRESA>` no app canônico;
7. aguardar conexão, executar migrations e sincronizar o diretório;
8. ativar o `EmpresaBanco` somente depois de todas as validações.

O cliente da API Railway foi separado da orquestração. O contrato atualmente
isolado em `RailwayApiClient.ensure_tcp_proxy` usa `tcpProxyCreate`; a API o
marca como legado e exige redeploy, portanto esse único método deve ser trocado
quando a Railway retirar o endpoint. Não espalhe essa mutation pelo código.

## Variáveis de configuração

- `RAILWAY_MULTI_PROJECT_ENABLED=False`: feature flag. O padrão desligado
  preserva integralmente o fluxo atual.
- `RAILWAY_CONTROL_PROJECT_TOKEN`: token usado para gravar a variável no app
  canônico. Por compatibilidade, usa `RAILWAY_PROJECT_TOKEN` quando omitido.
- `RAILWAY_PROJECT_TOKEN`: token do projeto principal.
- Para cada projeto adicional, usar um nome exclusivo, por exemplo
  `RAILWAY_PROJECT_TOKEN_BANCOS_02`.

Nunca coloque valores dessas variáveis neste documento, em logs, mensagens de
erro ou campos do Banco Gerencial.

## Estratégia de capacidade

No plano Hobby, configure `max_volumes=10` e `volumes_reservados=1`. Assim, o
provisionador usa no máximo nove volumes e preserva uma vaga para restauração ou
manutenção. A contagem inclui qualquer volume do projeto, não apenas bancos de
empresas.

Projetos exclusivamente de bancos não precisam hospedar uma cópia do app nem
Redis. Cada banco continua sendo um serviço e um volume independentes.

## Como adicionar o futuro projeto `Bancos 02`

Não execute estes passos até existir necessidade real e uma janela de teste:

1. criar um projeto Railway vazio no mesmo workspace;
2. confirmar o ambiente `production` e copiar seus IDs;
3. criar um Project Token restrito ao novo projeto;
4. adicionar o token como variável protegida no serviço canônico do
   `ited.app.br`, por exemplo `RAILWAY_PROJECT_TOKEN_BANCOS_02`;
5. na Central, abrir **Projetos de bancos** e cadastrar:
   - nome `Bancos 02`;
   - IDs exatos do projeto e ambiente;
   - `token_env_var=RAILWAY_PROJECT_TOKEN_BANCOS_02`;
   - conexão pública;
   - prioridade, limite 10 e reserva 1;
6. manter o registro inativo e fazer redeploy do app para carregar o token;
7. usar **Validar e ativar**;
8. habilitar `RAILWAY_MULTI_PROJECT_ENABLED=True` primeiro em stage;
9. criar uma empresa canária sem dados reais;
10. acompanhar serviço, volume, TCP Proxy e deployment específicos;
11. executar `check_tenant_databases`, migrations e testes de login;
12. confirmar isolamento consultando contagens da empresa canária e das demais;
13. testar backup/restauração e somente então habilitar em produção.

## Rollback e falhas parciais

- Desligar `RAILWAY_MULTI_PROJECT_ENABLED` faz novas solicitações voltarem ao
  fluxo legado; não move nem apaga bancos já criados.
- Nunca apague automaticamente serviço ou volume após falha. Primeiro descubra
  se houve criação parcial e retome pelo mesmo `EmpresaBanco`.
- Se o TCP Proxy falhar, mantenha a empresa fora do estado `ATIVO`.
- Se a variável do app for criada, mas migrations falharem, preserve o banco e
  corrija a causa antes de repetir.
- Se um projeto ficar indisponível, marque-o em manutenção para impedir novas
  alocações. Isso não muda os tenants que já pertencem a ele.
- Antes de qualquer exclusão, gere backup e confirme IDs por leitura no Railway.

## Testes e critérios de aceite

Cobertura adicionada:

- flag desligada preserva o provisionador legado;
- seleção respeita prioridade e capacidade reservada;
- pool sem vaga é recusado;
- banco remoto recebe TCP Proxy e URL com SSL;
- caracteres especiais de usuário/senha são escapados;
- testes anteriores do multibanco continuam passando.

Antes de publicar alterações futuras:

```powershell
python manage.py makemigrations --check --dry-run --settings=config.settings.test
python manage.py check --settings=config.settings.test
python manage.py test apps.core.tests.test_railway_multi_project --settings=config.settings.test
python manage.py test apps.core.tests.test_railway_provisioner apps.core.tests.test_empresa_banco_service apps.core.tests.test_multitenancy --settings=config.settings.test
git diff --check
```

Critério final para liberar o primeiro projeto remoto:

- projeto validado e ativo na Central;
- volume dentro da capacidade operacional;
- banco dedicado criado uma única vez;
- TCP Proxy ativo;
- conexão SSL aprovada;
- migrations concluídas;
- empresa acessível no seletor;
- dados ausentes nos bancos das outras empresas;
- backup e restauração testados;
- `ited.app.br/health/` saudável após o deploy.

## Estado do trabalho

- [x] modelo de projetos/pools;
- [x] vínculo do banco da empresa ao projeto;
- [x] seleção transacional por capacidade;
- [x] cliente Railway isolado;
- [x] criação idempotente de serviço, volume e TCP Proxy;
- [x] conexão remota com SSL;
- [x] painel de capacidade na Central e ação de validação;
- [x] feature flag desligada por padrão;
- [x] testes automatizados da fundação;
- [ ] homologação real em segundo projeto de stage;
- [ ] ativação em produção.

Os dois últimos itens dependem da criação deliberada de um segundo projeto e de
um tenant canário. A ausência deles não altera o funcionamento atual.
