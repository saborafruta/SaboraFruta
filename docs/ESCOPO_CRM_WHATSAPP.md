# Escopo funcional e técnico — CRM WhatsApp integrado à força de vendas

## 1. Objetivo

Transformar a rota existente `/crm/` na central comercial do iTED, integrando:

- atendimento humano por múltiplos números comerciais de WhatsApp;
- carteira exclusiva de clientes por vendedor;
- acompanhamento gerencial das conversas;
- criação e envio de orçamentos durante o atendimento;
- aprovação do orçamento pelo cliente;
- conversão segura do orçamento aprovado em pedido da força de vendas;
- acompanhamento do pedido até faturamento e entrega;
- indicadores comerciais e operacionais por vendedor, cliente e período.

O CRM deve preservar a experiência familiar do WhatsApp Web para reduzir a curva de aprendizado dos vendedores, sem perder as regras comerciais, de segurança e de auditoria do iTED.

## 2. Decisões já definidas

1. Cada vendedor utiliza um número comercial próprio da empresa.
2. O número geral da filial continua sendo o canal institucional da empresa e não pertence a um vendedor.
3. Os números do CRM dos vendedores formam um conjunto separado do número geral da filial.
4. O vendedor pode conversar pelo WhatsApp Business no celular ou pela central do iTED.
5. O iTED deve armazenar as mensagens recebidas e enviadas por ambos os canais.
6. O vendedor visualiza e atende somente clientes da própria carteira.
7. O gestor visualiza todos os números dos vendedores, conversas e resultados da equipe.
8. Os números dos vendedores serão conectados por QR Code, no mesmo fluxo de `Aparelhos conectados` do WhatsApp Web.
9. O CRM será construído sobre o módulo já existente em `/crm/`.
10. O orçamento aprovado deve alimentar o fluxo já existente da força de vendas.

## 2.1 Separação dos canais de WhatsApp

O iTED passará a reconhecer duas finalidades diferentes de conexão:

### Número geral da filial

- é o número institucional já configurado para a filial;
- continua atendendo às funções gerais já existentes no iTED;
- não fica vinculado exclusivamente a um vendedor;
- não deve ser misturado automaticamente à carteira ou às métricas individuais dos vendedores;
- mantém a regra atual de uma configuração geral por filial, salvo decisão futura específica.

### Números comerciais dos vendedores

- cada número pertence à empresa e fica operacionalmente associado a um vendedor;
- cada vendedor acessa somente o próprio número e a própria carteira;
- o gestor acessa todos os números comerciais da equipe;
- as conversas alimentam oportunidades, orçamentos, pedidos e indicadores do CRM;
- cada vendedor possui uma sessão isolada no gateway de QR Code, mantendo o uso do WhatsApp no celular e no iTED.

Arquitetura esperada:

```text
Filial
├── Número geral da empresa
│   └── Atendimento e comunicações institucionais existentes
│
└── CRM comercial
    ├── Vendedor A ── Número comercial A ── Carteira A
    ├── Vendedor B ── Número comercial B ── Carteira B
    └── Vendedor C ── Número comercial C ── Carteira C

Gestor
└── Supervisão de todos os números e carteiras do CRM comercial
```

## 3. Situação atual do iTED

O projeto já possui uma base relevante que deve ser reaproveitada.

### 3.1 CRM atual

A rota `/crm/` já contém:

- funil comercial configurável;
- oportunidades por cliente;
- responsável pela oportunidade;
- etapas de negócio;
- prioridades e tags;
- registro auditável de atividades;
- visão 360° do cliente;
- consulta aos pedidos do cliente;
- alertas de recompra.

Essas estruturas devem ser preservadas e incorporadas à nova central. O CRM não será refeito como um módulo concorrente.

### 3.2 Força de vendas atual

O modelo `PedidoVenda` já oferece:

- tipo `ORCAMENTO` e tipo `PEDIDO`;
- estados de rascunho, aguardando aprovação, aprovado, confirmado, separação, faturamento, entrega e encerramento;
- origem WhatsApp;
- cliente, vendedor, produtos, preços, descontos, pagamento, frete e entrega;
- chave de idempotência para impedir pedidos duplicados;
- geração de PDF;
- envio do documento por WhatsApp;
- dashboard da força de vendas com vendas, pedidos, ticket médio, clientes, margem, descontos, ranking, produtos e regiões.

O novo CRM deve consumir esses serviços e modelos. Não deve criar um segundo tipo de pedido ou um fluxo financeiro paralelo.

### 3.3 WhatsApp atual

O iTED já possui:

- suporte à Meta Cloud API oficial;
- suporte legado à Evolution API;
- conversas e mensagens persistidas no Banco Gerencial;
- identificação externa de mensagens para evitar duplicidade;
- armazenamento do evento recebido;
- associação de conversa a cliente;
- envio de documentos e PDF de pedido.

A configuração atual permite um número geral por filial. Essa regra atende ao canal institucional e deve ser preservada. Os números comerciais dos vendedores deverão usar uma estrutura separada, permitindo vários números na mesma empresa ou filial sem alterar a finalidade da configuração geral existente.

### 3.4 Estratégia de conexão por QR Code

Por decisão de experiência operacional, os números dos vendedores usarão conexão por QR Code, semelhante ao WhatsApp Web. O vendedor não informará conta Meta, identificadores ou credenciais técnicas. O fluxo será:

- o gestor associa o número comercial ao vendedor;
- o iTED cria uma sessão exclusiva para esse vendedor;
- o vendedor abre `WhatsApp > Aparelhos conectados > Conectar aparelho`;
- o vendedor lê o QR Code exibido pelo iTED;
- mensagens novas do celular e do CRM passam a compor o histórico comercial.

Essa modalidade utiliza uma sessão do WhatsApp Web por meio do gateway Evolution e não equivale à Cloud API oficial da Meta. Ela exige monitoramento de conexão e pode depender de nova leitura de QR Code quando a sessão for encerrada pelo WhatsApp. A chave do gateway permanece somente no servidor e não aparece para vendedores ou gestores.

Como a conexão dos vendedores usa uma sessão por QR Code, o CRM permitirá que a pessoa envie mensagens manuais mesmo depois de 24 horas. O iTED não enviará templates aprovados, cobranças ou automações para iniciar ou retomar uma conversa.

O número geral da filial não precisa ser migrado junto com os números dos vendedores. Cada canal terá conexão, credenciais, estado e estratégia de migração próprios.

Antes da expansão, o piloto deverá confirmar:

1. estabilidade da sessão individual do vendedor;
2. geração, renovação e leitura do QR Code;
3. funcionamento dos webhooks de mensagens, ecos e estados;
4. sincronização de texto, imagem, áudio e documentos;
5. comportamento de edição e exclusão;
6. histórico disponibilizado durante o onboarding;
7. envio manual depois de 24 horas, sem template nem automação;
8. continuidade do uso no celular;
9. ausência de impacto no número geral da filial.

## 4. Atores e permissões

### 4.1 Vendedor

Pode:

- acessar apenas o próprio número comercial;
- visualizar somente clientes da própria carteira;
- pesquisar somente clientes autorizados;
- ler e responder às próprias conversas;
- iniciar uma nova conversa pesquisando exclusivamente a própria carteira;
- conversar imediatamente com um cliente que ele mesmo acabou de cadastrar;
- enviar textos, imagens, documentos e áudios suportados;
- criar oportunidades e orçamentos para clientes da carteira;
- enviar orçamento para aprovação;
- acompanhar pedidos originados por seus atendimentos;
- consultar os próprios indicadores.

Não pode:

- abrir conversas de outros vendedores;
- pesquisar ou iniciar conversa com cliente de outra carteira pelo CRM;
- usar outro número comercial;
- alterar a titularidade de uma carteira;
- atribuir a si mesmo um cliente já existente;
- alterar mensagens já armazenadas;
- aplicar descontos fora das regras comerciais existentes.

### 4.2 Gestor comercial

Pode:

- visualizar todos os vendedores e números sob sua gestão;
- abrir e acompanhar todas as conversas;
- filtrar conversas por vendedor, número, cliente, status e período;
- responder em nome de qualquer vendedor sob sua gestão;
- definir ou corrigir o vendedor titular de um cliente;
- acompanhar orçamentos, aprovações, pedidos e vendas;
- visualizar dashboards e rankings;
- consultar alertas e ocorrências de contato fora da carteira;
- auditar mensagens enviadas pelo celular e pelo CRM.

### 4.3 Administrador

Pode, além das ações do gestor:

- conectar e desconectar números;
- associar números a vendedores;
- acompanhar sessões, QR Codes, webhooks e falhas de conexão;
- configurar permissões e alçadas;
- definir políticas de retenção e acesso;
- consultar falhas técnicas e custos de mensageria.

## 5. Regra de carteira de clientes

A empresa possui a base de clientes. Dentro dessa base, cada cliente fica associado ao vendedor responsável pelo atendimento comercial. O conjunto de clientes associados a um vendedor forma a carteira individual desse vendedor.

Em outras palavras:

- o cliente pertence ao cadastro da empresa;
- o vínculo comercial indica qual vendedor atende aquele cliente;
- cada vendedor acessa apenas os clientes vinculados a ele;
- o gestor acessa todas as carteiras e clientes da empresa;
- no CRM, o mesmo cliente não fica simultaneamente na carteira de dois vendedores.

Não haverá fluxo de transferência de carteira nem substituição temporária. Quando outro vendedor passar a atender determinado cliente, o gestor ou administrador fará uma reatribuição simples daquele cliente antes do novo contato. O vínculo anterior será substituído, sem processo de aceite entre vendedores.

Somente gestor ou administrador pode atribuir e reatribuir clientes já existentes. Quando o próprio vendedor cadastra um cliente novo, o iTED cria automaticamente o vínculo desse novo cadastro com a carteira do vendedor criador. Esse vínculo automático não permite que ele tome para si um cadastro que já existia.

Toda operação do backend deve validar essa propriedade. Não basta esconder o cliente na interface.

As seguintes ações exigem autorização de carteira:

- pesquisar cliente;
- abrir conversa;
- iniciar conversa;
- enviar mensagem;
- criar oportunidade;
- criar ou editar orçamento;
- consultar pedido;
- enviar documento comercial.

### 5.1 Contato fora da carteira pelo celular

O WhatsApp Business não permite ao iTED impedir previamente que o vendedor digite qualquer número no celular.

Quando isso ocorrer, o iTED deverá:

1. receber a mensagem enviada pelo celular no webhook da sessão;
2. armazenar a mensagem normalmente;
3. verificar a titularidade do cliente;
4. registrar uma ocorrência de contato fora da carteira;
5. avisar o gestor;
6. impedir a continuidade pelo CRM sem autorização.

Portanto, o CRM oferece prevenção completa dentro do iTED e detecção auditável para mensagens iniciadas diretamente no celular.

## 6. Central de conversas do vendedor

A tela principal do vendedor deve adotar o padrão de interação do WhatsApp Web.

### 6.1 Coluna esquerda — conversas

- número comercial ativo e situação da conexão;
- pesquisa por cliente autorizado;
- filtros de não lidas, aguardando resposta, orçamento pendente e venda concluída;
- nome, última mensagem, horário e quantidade de não lidas;
- marcador da etapa comercial;
- indicação de conversa iniciada fora da carteira, quando aplicável.

### 6.2 Coluna central — chat

- cabeçalho com cliente, telefone e indicação da última atividade;
- histórico cronológico de mensagens;
- indicação interna da origem: celular, CRM, automação ou sistema;
- status de envio, entrega e leitura;
- visualização de texto, imagem, áudio e documento;
- registro de mensagem editada ou apagada;
- resposta contextual, quando suportada;
- campo de composição fixo;
- anexos, áudio e envio;
- aviso de que contatos após 24 horas continuam sendo exclusivamente manuais.

### 6.3 Coluna direita — contexto comercial

- dados e situação cadastral do cliente;
- vendedor e carteira;
- limite e saldo de crédito, quando autorizado;
- últimas compras;
- oportunidades abertas;
- orçamentos e pedidos relacionados;
- alertas de recompra;
- próxima ação comercial;
- botão `Novo orçamento`.

## 7. Sincronização das mensagens

### 7.1 Mensagem recebida do cliente

1. O gateway da sessão envia o webhook ao iTED.
2. O iTED valida a URL secreta da conexão.
3. O evento bruto é armazenado para auditoria.
4. A mensagem é deduplicada pelo identificador externo.
5. O número comercial e o cliente são identificados.
6. A conversa é criada ou atualizada.
7. A mensagem aparece em tempo real no CRM.
8. O vendedor responsável recebe a notificação.

### 7.2 Mensagem enviada pelo celular

1. O vendedor envia pelo WhatsApp Business.
2. O gateway envia o evento da mensagem ao iTED.
3. O iTED armazena a mensagem como saída com origem `celular`.
4. A autorização de carteira é conferida.
5. A tela do CRM é atualizada em tempo real.
6. Uma ocorrência é criada se o destinatário não pertencer à carteira.

### 7.3 Mensagem enviada pelo CRM

1. O vendedor escreve a mensagem.
2. O backend valida empresa, número, vendedor e carteira.
3. A mensagem é persistida como pendente.
4. O gateway da sessão realiza o envio humano.
5. O identificador externo e os estados posteriores são armazenados.
6. A mensagem aparece também no WhatsApp conectado ao celular.

Mesmo depois de 24 horas sem mensagem do cliente, o campo permanece disponível para uma ação manual do vendedor. O iTED não substituirá o texto por modelo aprovado, não disparará cobrança e não tentará iniciar ou retomar a conversa automaticamente.

### 7.4 Edição e exclusão

O conteúdo originalmente recebido deve permanecer preservado no histórico de auditoria. Quando o gateway fornecer evento de edição ou revogação, o CRM deve apresentar a situação atual sem eliminar o conteúdo original do registro auditável.

Há uma limitação externa importante: a disponibilidade de eventos de edição e exclusão precisa ser confirmada no piloto para cada tipo de mensagem e dispositivo. O iTED preservará e sinalizará o conteúdo quando receber esses eventos; não deve prometer detectar uma exclusão que o WhatsApp ou o gateway não notifique.

## 8. Fluxo comercial: conversa, orçamento e pedido

### 8.1 Criação do orçamento

Dentro da conversa, o vendedor seleciona `Novo orçamento` e informa:

- cliente já vinculado à conversa;
- produtos e quantidades;
- tabela e preços autorizados;
- descontos permitidos pelas regras comerciais vigentes no iTED;
- forma e condição de pagamento;
- frete e endereço de entrega;
- validade;
- previsão de entrega;
- observações para o cliente;
- observações internas.

O orçamento deve ser criado em `PedidoVenda` com:

- tipo `ORCAMENTO`;
- origem `WHATSAPP`;
- status inicial de rascunho;
- vendedor responsável;
- vínculo com a conversa e com a oportunidade;
- fotografia dos preços, descontos e condições daquela versão.

### 8.2 Envio para aprovação

Ao escolher `Enviar para aprovação`:

1. o iTED valida produtos, valores, condições e regras comerciais vigentes;
2. cria uma versão imutável do orçamento;
3. altera o status para `AGUARDANDO_APROVACAO`;
4. gera PDF e resumo legível no WhatsApp;
5. gera um link público seguro, temporário e de uso controlado;
6. envia o orçamento na conversa;
7. registra mensagem, versão, horário e vendedor.

### 8.3 Página pública do cliente

O cliente abre o link sem precisar ser usuário do iTED e visualiza:

- empresa e vendedor;
- número e validade do orçamento;
- produtos, quantidades e preços;
- descontos, frete e total;
- pagamento e previsão de entrega;
- observações e termos;
- ações `Aprovar orçamento`, `Solicitar alteração` e `Recusar`.

A aprovação pelo link deve exigir uma confirmação explícita do cliente. O orçamento também poderá ser aprovado manualmente dentro do iTED quando o cliente confirmar por mensagem, áudio, telefone ou outro meio acompanhado pelo vendedor.

Na aprovação manual, o iTED deverá registrar:

- usuário que confirmou a aprovação;
- data e hora;
- versão aprovada;
- forma da confirmação;
- mensagem relacionada ou observação de evidência, quando disponível.

### 8.4 Aprovação e criação do pedido

Ao aprovar:

1. o token público é validado;
2. o iTED verifica se a versão continua válida e aguardando aprovação;
3. grava data, hora, versão, IP e identificação informada pelo aprovador;
4. torna o token inutilizável para uma segunda aprovação;
5. converte o mesmo `PedidoVenda` de tipo `ORCAMENTO` para tipo `PEDIDO`, preservando a fotografia aprovada;
6. utiliza chave de idempotência para impedir duplicidade;
7. associa o pedido à conversa, oportunidade, cliente e vendedor;
8. move a oportunidade para ganha;
9. marca a origem do pedido como `WHATSAPP` e registra a identificação da conversa;
10. coloca o pedido no fluxo da força de vendas.

As consultas e dashboards da força de vendas deverão considerar pedidos de origem `WHATSAPP` junto aos pedidos externos. Não haverá envio automático de mensagem em mudanças de status no primeiro momento; qualquer comunicação será iniciada manualmente pelo vendedor ou gestor.

Fluxo esperado:

`Rascunho → Enviado → Aguardando aprovação → Aprovado → Aguardando separação → Separado → Faturado → Em entrega → Entregue`

Pedidos recusados ou expirados permanecem disponíveis para auditoria e análise de conversão.

## 9. Visão do gestor

O gestor terá duas áreas complementares.

### 9.1 Supervisão de conversas

- todos os números e respectivos vendedores;
- situação de conexão de cada número;
- mensagens em tempo real;
- conversas não respondidas;
- tempo desde a última mensagem do cliente;
- clientes atendidos hoje;
- contatos fora da carteira;
- orçamentos aguardando aprovação;
- possibilidade de assumir ou responder, conforme permissão.

### 9.2 Dashboard do CRM

Filtros:

- hoje, semana, mês ou período personalizado;
- vendedor;
- filial;
- número de WhatsApp;
- região;
- etapa do funil;
- situação do orçamento ou pedido.

Indicadores de atendimento:

- clientes únicos atendidos;
- conversas iniciadas e recebidas;
- mensagens recebidas e enviadas;
- tempo médio e mediano da primeira resposta;
- conversas sem resposta;
- clientes da carteira sem contato no período;
- contatos fora da carteira;
- origem das respostas: celular ou CRM.

Indicadores comerciais:

- orçamentos criados;
- orçamentos enviados;
- orçamentos aprovados, recusados e expirados;
- valor orçado;
- valor aprovado;
- taxa de conversão por quantidade e valor;
- vendas e quantidade de pedidos;
- ticket médio;
- descontos concedidos;
- margem estimada;
- tempo médio entre primeiro contato, orçamento e aprovação;
- ranking de vendedores;
- produtos e regiões com maior venda.

Indicadores de carteira:

- cliente campeão de cada vendedor por faturamento;
- cliente com maior número de pedidos;
- cliente com maior ticket médio;
- clientes novos;
- clientes reativados;
- clientes sem compra recente;
- concentração de receita nos maiores clientes;
- oportunidades de recompra.

O dashboard existente da força de vendas deve ser reaproveitado. Os indicadores de conversa e orçamento serão adicionados, evitando duplicar cálculos já disponíveis.

## 10. Alterações estruturais necessárias

### 10.1 Números comerciais

Preservar `ConfiguracaoWhatsApp` como configuração do número geral da filial e criar uma entidade própria para as conexões comerciais do CRM, por exemplo `ConexaoWhatsAppVendedor`.

Essa separação evita que uma evolução do CRM altere o funcionamento do WhatsApp geral já utilizado pela empresa. Cada conexão comercial deverá ter, no mínimo:

- empresa e filial;
- número comercial;
- vendedor responsável;
- situação da conexão;
- identificador isolado da sessão de QR Code;
- data da última sincronização;
- permissões de supervisão;
- janela de atendimento humano.

As conversas deverão identificar explicitamente se pertencem ao canal geral ou ao CRM comercial. Indicadores individuais de vendedor considerarão somente as conexões comerciais, salvo filtro explícito do gestor.

### 10.2 Carteira

Criar ou formalizar uma associação auditável entre o cliente da empresa e seu vendedor responsável, contendo:

- empresa proprietária do cadastro;
- cliente;
- vendedor titular;
- origem da atribuição;
- autor da alteração;
- data da última alteração.

Não será criado um fluxo de transferência ou substituição. Uma reatribuição administrativa apenas substitui o vendedor responsável daquele cliente. A carteira continua sendo individual por vendedor.

### 10.3 Mensagens

Ampliar o registro atual para representar:

- origem do envio;
- autor interno;
- tipo de evento;
- mensagem original relacionada;
- edição ou revogação;
- data enviada pela plataforma;
- entrega e leitura;
- anexos e metadados;
- classificação de custo e janela de atendimento.

### 10.4 Vínculos comerciais

Formalizar os vínculos entre:

- conversa;
- oportunidade;
- versão do orçamento;
- pedido de venda;
- aprovação pública.

Como as conversas vivem no Banco Gerencial e os pedidos vivem no banco operacional de cada empresa, os vínculos devem respeitar a arquitetura multibanco e nunca depender de chave estrangeira entre bancos.

## 11. Segurança e auditoria

- URL de webhook secreta e exclusiva por conexão;
- chave do gateway armazenada somente no ambiente do servidor;
- autorização no backend em todas as ações;
- segregação por empresa e filial;
- deduplicação de webhooks e envios;
- trilha de auditoria imutável;
- token de aprovação com expiração e uso único;
- criação idempotente de pedido;
- registro da última reatribuição administrativa da carteira;
- registro de respostas do gestor em nome do vendedor;
- política de retenção e acesso compatível com a LGPD;
- não exposição de eventos brutos ou dados de outro cliente na interface.

## 12. Fases de implementação

### Fase 1 — Fundação e número piloto

- preservar e testar o funcionamento do número geral da filial antes de qualquer mudança;
- escolher um número comercial de vendedor para o piloto do CRM;
- habilitar a sessão isolada e o QR Code do vendedor;
- criar a estrutura separada de conexões comerciais, permitindo múltiplos números por filial;
- associar número a vendedor;
- receber mensagens, ecos e estados;
- validar sincronização entre celular e CRM;
- ampliar armazenamento e deduplicação.

### Fase 2 — Caixa de entrada operacional

- reformular `/crm/` com navegação da central;
- implementar lista de conversas e chat em tempo real;
- permitir envio de texto e anexos;
- mostrar origem, entrega, leitura, edição e exclusão;
- aplicar permissões de vendedor e gestor.

### Fase 3 — Carteiras

- vincular cada cliente da empresa ao seu vendedor responsável;
- formar a carteira individual de cada vendedor a partir desses vínculos;
- restringir buscas e envios no backend;
- permitir reatribuição administrativa simples pelo gestor;
- detectar contato indevido originado no celular;
- criar alertas gerenciais.

### Fase 4 — Orçamento e aprovação

- abrir o editor de orçamento dentro da conversa;
- reaproveitar `PedidoVenda`, catálogo, preço e condições;
- criar versionamento do orçamento;
- gerar PDF e link seguro;
- implementar aprovação pública por link e aprovação manual auditada;
- converter aprovação em pedido idempotente.

### Fase 5 — Integração com a operação

- ligar pedido aprovado ao Kanban da força de vendas;
- atualizar etapas da oportunidade automaticamente;
- exibir no CRM as mudanças relevantes do pedido, sem mensagens automáticas;
- apresentar separação, faturamento e entrega no CRM.

### Fase 6 — Dashboard gerencial

- reaproveitar indicadores existentes da força de vendas;
- acrescentar métricas de conversa, resposta e orçamento;
- criar ranking e visão por vendedor;
- calcular clientes campeões e oportunidades de carteira;
- adicionar alertas e comparativos por período.

### Fase 7 — Expansão

- validar o piloto com usuários reais;
- corrigir falhas de sincronização e experiência;
- conectar os demais números;
- acompanhar custos, qualidade e estabilidade;
- desativar integrações não oficiais onde não forem mais necessárias.

## 13. Critérios de aceite do MVP

O MVP estará apto ao piloto quando:

1. um número comercial funcionar simultaneamente no celular e no CRM;
2. mensagens dos dois canais aparecerem uma única vez no iTED;
3. o vendedor visualizar somente sua carteira;
4. o gestor visualizar a conversa completa;
5. mensagens e estados permanecerem armazenados;
6. o vendedor criar e enviar um orçamento pela conversa;
7. uma versão específica ser aprovada pelo cliente no link ou manualmente pelo vendedor;
8. a aprovação gerar somente um pedido na força de vendas;
9. o pedido seguir para separação, faturamento e entrega;
10. o gestor consultar atendimentos, orçamentos e vendas do vendedor piloto.

## 14. Itens fora do primeiro MVP

- disparos de marketing em massa;
- chatbot ou atendimento autônomo por IA;
- chamadas de voz ou vídeo;
- grupos de WhatsApp;
- pagamento dentro do WhatsApp;
- campanhas complexas e automações de nutrição;
- substituição integral dos dashboards corporativos já existentes.

Esses itens podem ser incorporados depois que o fluxo humano de atendimento e venda estiver estável.

## 15. Decisões de implementação

### 15.1 Decisões confirmadas

1. O gestor poderá ler e responder em nome dos vendedores sob sua gestão.
2. Cada vendedor terá sua própria carteira, formada pelos clientes da empresa vinculados a ele.
3. Cada cliente terá um único vendedor responsável no CRM.
4. Não haverá transferência ou substituição temporária; apenas reatribuição administrativa simples quando mudar o vendedor responsável pelo cliente.
5. O orçamento poderá ser aprovado pelo link ou manualmente no iTED, sempre com auditoria.
6. O mesmo `PedidoVenda` será convertido de orçamento em pedido e seguirá para a força de vendas.
7. O pedido terá origem `WHATSAPP` e vínculo com a conversa.
8. Não haverá mensagens automáticas por mudança de status no primeiro momento.
9. O CRM dos vendedores enviará somente mensagens digitadas por pessoas, inclusive após 24 horas; não usará modelos aprovados nem automações.

### 15.2 Decisões que não impedem o início

#### Descontos

A nova alçada de descontos ainda não está definida. O MVP utilizará as regras comerciais e permissões já existentes no iTED. Uma política específica de alçada poderá ser acrescentada posteriormente sem bloquear a fundação do CRM.

#### Retenção de mensagens e anexos

Retenção é o período durante o qual o iTED continuará guardando a conversa e seus arquivos depois de recebê-los, mesmo que sejam apagados no celular ou que o cliente deixe de comprar.

Exemplos de política:

- guardar por cinco anos e apagar depois do prazo;
- guardar enquanto a empresa utilizar o iTED;
- permitir que cada empresa configure o prazo;
- guardar textos por mais tempo e anexos pesados por um período menor.

Até a política ser definida, o MVP não fará exclusão automática. O modelo deverá manter datas e metadados que permitam implantar uma política configurável posteriormente, respeitando necessidades de auditoria, custo de armazenamento e LGPD.
