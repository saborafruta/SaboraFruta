# PDV offline e contingência fiscal

Status do documento: plano vivo e registro de implementação

Última atualização: 18/09/2026

Responsáveis funcionais: Operação, Fiscal e Tecnologia

## 1. Objetivo

Este documento registra a estratégia do Sabor a Fruta para continuar vendendo com instabilidade de internet, indisponibilidade temporária do ERP ou queda de energia, sem duplicar vendas, estoque ou documentos fiscais.

O desenho é híbrido:

1. PWA no navegador para reduzir instalação e suporte por computador.
2. Armazenamento local no navegador para proteger carrinho, catálogo e fila de vendas.
3. Servidor central como fonte oficial de estoque, financeiro, numeração e documentos fiscais.
4. Contingência hospedada da NFC-e pela Focus NFe.
5. Comunicador Offline da Focus como camada futura para os caixas que precisarem emitir NFC-e durante perda completa de internet.

NF-e modelo 55 não faz parte do fluxo automático de contingência do PDV. Deve ser tratada separadamente, com suporte formal da Focus e validação do contador para as regras aplicáveis ao RN.

## 2. Princípios que não podem ser quebrados

- Toda venda recebe um identificador local único antes de ser enviada ao servidor.
- Repetir uma requisição com o mesmo identificador nunca pode criar outra venda ou outra baixa de estoque.
- Uma resposta de rede perdida não significa que a venda falhou; primeiro é necessário consultar ou reenviar com o mesmo identificador.
- Dados locais são separados por filial e usuário.
- O navegador nunca deve armazenar custo ou margem no catálogo offline.
- Página autenticada de um usuário não deve ser servida indiscriminadamente para outro usuário pelo cache do service worker.
- Catálogo offline vencido não pode autorizar novas vendas.
- TEF, cashback, crédito, boleto, crediário e operações que dependem de confirmação externa não podem ser tratados como confirmados offline.
- Venda comercial e emissão fiscal são estados diferentes. A venda pode estar registrada e o documento fiscal ainda estar processando.

## 3. Identificadores

### 3.1 Instalação

Na primeira utilização, o navegador cria um `installation_id` aleatório e persistente no IndexedDB. Ele identifica a instalação lógica do PDV naquele perfil do navegador.

Formatar o computador, limpar os dados do navegador ou usar outro perfil cria uma instalação nova. Isso não apaga vendas já sincronizadas no servidor. Vendas que existam somente no armazenamento local precisam ser sincronizadas ou exportadas antes da formatação.

### 3.2 Escopo local

Os dados locais usam o escopo:

```text
filial_id:usuario_id
```

Isso impede que o rascunho de uma filial ou operador seja restaurado no contexto de outro.

### 3.3 Venda

Cada venda recebe um `local_id` criptograficamente aleatório, normalmente iniciado por `pdv`. O mesmo valor é enviado:

- no corpo como `idempotency_key`;
- no cabeçalho HTTP `Idempotency-Key`;
- na fila local, quando a venda não puder ser confirmada imediatamente.

O banco central possui restrição única para essa chave. Ela é gravada junto com a venda antes da baixa de estoque. Em uma corrida entre duas requisições iguais, somente uma produz efeitos; a outra recupera a venda vencedora.

### 3.4 Caixa e sessão

O caixa continua sendo um cadastro central. A sessão de caixa deve ser aberta online antes da operação offline. O snapshot local guarda o identificador da sessão e do caixa para associar corretamente as vendas quando a conexão retornar.

Uma máquina formatada ou sem dados locais não herda automaticamente a identidade operacional antiga. O operador deve entrar novamente, escolher a filial e abrir o caixa online.

## 4. Estados apresentados no PDV

O indicador superior possui três estados visuais:

- `ONLINE`: bolinha verde estável. O servidor respondeu e não há sincronização ativa.
- `SEM INTERNET`: bolinha vermelha piscando. As operações permitidas usam dados locais.
- `SINCRONIZANDO`: ícone amarelo girando. O catálogo ou a fila de vendas está sendo reconciliado com o servidor.

Quando houver vendas aguardando envio, o indicador mostra a quantidade. Clicar nele permite consultar o estado, o erro mais recente e solicitar nova tentativa.

## 5. O que já está implementado

### Fase 1 — proteção da venda em andamento

- PWA instalável pelo navegador.
- Carrinho persistido em IndexedDB desde o primeiro item.
- Solicitação de armazenamento persistente ao navegador.
- Transações locais com durabilidade estrita quando suportada.
- Recuperação do carrinho após atualização da página, fechamento inesperado ou reinicialização.
- Identificador único por venda.
- Finalização idempotente no PDV visual e na API REST.
- Preservação de resultado incerto quando a conexão cai durante a finalização.

### Fase 2 — catálogo e fila offline controlada

- Snapshot local do catálogo, sessão e formas de pagamento.
- Atualização do catálogo em páginas de até 200 produtos.
- Busca local por descrição, código, código de barras e identificador.
- Leitura de código de barras com fallback local.
- Remoção de custo e margem antes da gravação local.
- Catálogo local válido por no máximo 12 horas para autorizar entrada na fila.
- Fila durável de vendas no IndexedDB.
- Sincronização ao evento de retorno da internet e verificação periódica.
- Retentativas controladas e consulta manual de erros.
- Reenvio com a mesma chave de idempotência.

## 6. Operações permitidas offline nesta fase

Uma venda pode entrar na fila offline somente quando todas as condições forem verdadeiras:

- caixa aberto anteriormente com conexão;
- catálogo local válido;
- modo venda normal;
- consumidor final;
- sem delivery;
- sem venda fora do estabelecimento;
- sem comanda;
- sem edição de venda anterior;
- sem bonificação;
- sem cashback ou crédito;
- forma de pagamento existente no snapshot e sem TEF;
- forma de pagamento não classificada como boleto, vale, cashback, crediário ou convênio.

Se uma condição não for atendida, o carrinho é preservado, mas a venda não é considerada concluída offline. O operador recebe o motivo do bloqueio.

## 7. Sincronização de vendas

Fluxo normal:

```text
Carrinho local
  -> validações de risco offline
  -> fila local com local_id
  -> retorno da internet
  -> POST com Idempotency-Key
  -> venda/estoque/financeiro no servidor
  -> remoção da fila local
```

Se a resposta do servidor se perder depois de ele concluir a venda, a nova tentativa usa o mesmo `local_id`. O servidor responde com a venda existente em vez de baixar o estoque novamente.

Erros de negócio retornados pelo servidor permanecem visíveis na fila. Depois de três recusas automáticas, o item aguarda intervenção manual para evitar repetição infinita.

## 8. Queda de energia

O IndexedDB reduz a perda do carrinho e da fila após desligamento abrupto. A aplicação aguarda a confirmação da gravação local antes de tentar finalizar uma venda.

Ainda assim, a proteção operacional recomendada é:

- nobreak no computador ou terminal do caixa;
- nobreak no roteador, ONU/modem e switch;
- proteção elétrica para impressora e equipamentos fiscais;
- teste trimestral de autonomia das baterias;
- procedimento de encerramento e conferência após retorno de energia.

Se o PDV já estiver carregado, ele usa catálogo e fila local durante a queda de internet. A abertura fria offline também está disponível para perfis previamente autorizados: após reiniciar o computador sem internet, o service worker entrega um casco público sem dados embutidos, e o operador desbloqueia os dados locais com seu PIN desta instalação.

O casco offline não substitui a tela autenticada nem guarda seu HTML em cache. Ele permite apenas venda normal para Consumidor Final, usando o último caixa aberto, catálogo ainda válido e formas de pagamento que não dependam de autorização externa. Quando a internet retorna, ele redireciona para o PDV normal, que sincroniza a fila com a mesma chave de idempotência.

## 9. Contingência hospedada da NFC-e

### 9.1 Fluxo atual

1. O sistema tenta emitir a NFC-e normalmente pela Focus.
2. Erros de validação fiscal não habilitam contingência; precisam ser corrigidos.
3. Erro de rede ou indisponibilidade de servidor habilita a opção de contingência.
4. A emissão em contingência reutiliza a venda e mantém controle do número fiscal.
5. O documento fica identificado como em contingência/processando até a autorização definitiva.
6. A reconciliação automática consulta documentos recentes em processamento.
7. Se a Focus responder que a referência não existe e o envio anterior era incerto, o sistema reenvia o snapshot original sem trocar o número.

O disparo da contingência hospedada ainda exige confirmação do operador na interface. A reconciliação posterior é automática. Automatizar também a decisão de entrada em contingência somente deve ocorrer depois de homologação fiscal e de uma regra clara de tempo, tipos de erro e retorno ao modo normal.

### 9.2 Numeração e duplicidade

- A numeração é reservada atomicamente no servidor.
- Documento autorizado é reutilizado, nunca reemitido.
- Documento processando é consultado pela mesma referência.
- Falha HTTP após envio mantém o documento e o número para reconciliação.
- Uma nova numeração não deve ser criada apenas porque o navegador não recebeu a resposta.

### 9.3 Monitoramento necessário

- documentos processando por mais de 5 minutos;
- documentos em contingência ainda não autorizados;
- rejeições fiscais;
- falhas repetidas de autenticação Focus;
- divergência ou salto de numeração;
- venda sincronizada sem documento fiscal quando a emissão era obrigatória.

## 10. Comunicador Offline da Focus

O instalador ainda está pendente de recebimento do suporte da Focus. Quando for entregue:

1. validar origem, assinatura digital, versão e hash do arquivo;
2. armazenar o binário em local versionado e privado, não diretamente no repositório Git;
3. cadastrar versão, hash, data de publicação e instruções no sistema;
4. disponibilizar o download em Configuração Fiscal apenas para usuários autorizados;
5. registrar qual instalação/caixa confirmou a instalação e qual versão está ativa;
6. testar comunicação, emissão, impressão, retorno da internet e reconciliação;
7. documentar atualização e desinstalação;
8. definir procedimento para máquina formatada ou substituída.

O Comunicador não substitui a fila local do PDV. Ele acrescenta a capacidade fiscal local. O desenho final esperado é:

```text
PWA local-first -> fila comercial local -> Comunicador Focus -> NFC-e offline
       |                    |                       |
       +-------- reconciliação com ERP e Focus quando a internet voltar
```

## 11. Próximas fases

### Fase 3 — abertura fria offline (implementada)

- casco offline específico e sem HTML autenticado em cache;
- perfil vinculado a filial, operador e `installation_id`;
- ativação explícita no indicador de conexão do PDV;
- PIN local de seis dígitos derivado com PBKDF2 e prova protegida por AES-GCM;
- bloqueio temporário após cinco tentativas incorretas;
- autorização por 12 horas, renovada ao abrir o PDV conectado com caixa aberto;
- restauração de carrinho, catálogo, caixa, pagamentos permitidos e fila;
- bloqueio de novas vendas com catálogo vencido ou caixa ausente;
- retorno automático ao PDV online para sincronização quando a conexão volta.

Procedimento por operador e filial:

1. entrar no PDV com internet e abrir o caixa;
2. aguardar a atualização do catálogo;
3. clicar no indicador `ONLINE` e escolher `Ativar abertura offline`;
4. criar e confirmar um PIN local de seis dígitos;
5. testar uma abertura sem internet antes de colocar o caixa em produção.

O PIN fica somente neste navegador e não é a senha do ERP. Limpar os dados do navegador ou formatar a máquina remove a autorização e exige nova ativação online.

### Fase 4 — operação e auditoria

- painel administrativo de instalações/caixas;
- revogação de instalação perdida ou formatada;
- exportação de emergência da fila local;
- alerta central de vendas presas ou catálogo vencido;
- relatório de testes de contingência por filial;
- trilha de auditoria de tentativas e reconciliações.

### Fase 5 — Comunicador Focus

- download versionado na configuração fiscal;
- verificação automática de presença e versão;
- diagnóstico de porta/serviço local;
- emissão fiscal offline homologada;
- sincronização fiscal e comercial conjunta;
- runbook de suporte para troca ou formatação do computador.

## 12. Cenários obrigatórios de homologação

Antes de liberar operação offline ampla em uma filial, testar:

1. retirar o cabo durante a montagem do carrinho;
2. retirar o cabo imediatamente antes de finalizar;
3. servidor concluir a venda e a resposta não chegar ao navegador;
4. criar duas vendas offline e reconectar;
5. fechar e reabrir o navegador com venda na fila;
6. desligar abruptamente o computador com carrinho aberto;
7. catálogo com mais de 12 horas;
8. tentativa offline com TEF, cashback, crédito e cliente identificado;
9. erro de estoque na sincronização;
10. sessão de caixa fechada antes da sincronização;
11. queda durante emissão normal de NFC-e;
12. entrada e saída da contingência;
13. formatação/substituição de uma máquina;
14. dois caixas trabalhando simultaneamente na mesma filial.

Cada teste deve registrar filial, caixa, instalação, horário, `local_id`, resultado esperado, resultado obtido e responsável.

## 13. Arquivos de referência no código

- `static/js/pdv_local_store.js`: IndexedDB, instalação, rascunho, snapshot e fila.
- `static/sw.js`: cache do casco estático e política de navegação.
- `apps/pdv/templates/pdv/home.html`: experiência local-first e sincronização.
- `apps/pdv/services/venda_pdv_service.py`: criação transacional e idempotência.
- `apps/pdv/views/pdv.py`: endpoints do PDV e contingência fiscal.
- `apps/pdv/services/nfce_payload_builder.py`: emissão NFC-e e preservação de resultado incerto.
- `apps/fiscal/services/focusnfe_service.py`: integração e estados Focus.
- `apps/fiscal/tasks.py`: reconciliação automática.
- `apps/fiscal/services/nfce_scheduler.py`: execução periódica da reconciliação.

## 14. Critério de conclusão

O projeto será considerado maduro quando uma filial conseguir operar durante queda de internet e energia sem:

- perder carrinho ou venda confirmada;
- duplicar venda, estoque, financeiro ou NFC-e;
- aceitar pagamento que dependa de autorização indisponível;
- usar catálogo vencido sem aviso e bloqueio;
- misturar dados entre filial, usuário ou instalação;
- depender de intervenção técnica para uma reconciliação normal;
- deixar documento fiscal pendente sem alerta e responsável.
