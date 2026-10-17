# ITED Força de Vendas — Android

Aplicativo Android nativo integrado ao ERP ITED.

## Parâmetros da primeira versão

- pacote: `br.com.ited.forcavendas`;
- Android mínimo: 9 / API 28;
- Android alvo e compilação: 16 / API 36;
- interface: Kotlin + Jetpack Compose;
- tema claro: branco/cinza com laranja;
- tema escuro: preto/cinza com azul;
- API padrão: `https://ited.app.br/`, substituível pela propriedade Gradle
  `ITED_API_BASE_URL`.

## Ambiente necessário

- JDK 17;
- Android SDK Platform 36 e Build Tools 36;
- Android Studio compatível com AGP 9.4;
- Gradle 9.6.

O wrapper do Gradle já faz parte do projeto. Para gerar a versão técnica de
desenvolvimento:

```powershell
.\gradlew.bat :app:assembleDebug
```

O APK será criado em `app/build/outputs/apk/debug/app-debug.apk`.

## Princípios de sincronização

- o ITED online é a fonte oficial;
- dados mestres recebidos do servidor são somente leitura no cache local;
- rascunhos e operações ainda não confirmadas ficam na fila local;
- um pedido só aparece como recebido após o servidor devolver seu ID e número;
- toda escrita usa identificador idempotente para não duplicar pedidos;
- preço, crédito, estoque, depósito e lote são revalidados no servidor;
- a baixa definitiva da separação exige conexão.

## Funcionalidades da versão 0.5

- login JWT com o mesmo usuário do ITED;
- seleção segura de empresa e filial pelo Banco Gerencial;
- busca de clientes por ID, nome, CPF/CNPJ ou celular;
- consulta independente da carteira, crédito, bloqueio e tabela do cliente;
- busca de produtos por ID, código, código de barras ou nome;
- cliente e produtos só aparecem depois da pesquisa, com fechamento dos
  resultados ao tocar fora;
- descoberta de produtos por Destaques, Todos ou categoria, seguindo o mesmo
  fluxo da força de vendas mobile no navegador;
- estoque detalhado por depósito na busca e nos itens adicionados, destacando
  com ✓ o depósito que atenderá o pedido;
- alternância persistente entre tema claro e escuro pela tela inicial;
- rascunho local identificado em Meus pedidos, com ações para continuar ou
  excluir;
- categorias apresentadas em uma lista suspensa, com os produtos do filtro
  exibidos logo abaixo e fechamento ao tocar fora;
- interface de novo pedido reorganizada em cartões compactos e proporcionais;
- logo preservada com contorno branco adaptado para o tema escuro;
- preço comercial calculado pelo servidor para o cliente selecionado;
- criação de pedido externo com forma e condição de pagamento;
- rascunho local recuperável, inclusive depois de fechar a tela;
- fila local de pedidos quando a conexão falha, com envio idempotente;
- sincronização manual e automática a cada 15 minutos quando houver rede;
- Central de sincronização com horário, dados locais, tentativas e erros de envio;
- acompanhamento pelo número oficial devolvido pelo ITED;
- fila de separação com saldo por depósito e lotes vigentes;
- escolha manual, divisão entre vários lotes ou baixa automática por FEFO;
- conclusão online da separação com nova validação de estoque.

O cache separa cada pesquisa de cliente e produto, evitando que uma consulta
offline reutilize o resultado de outra busca. Pedidos pendentes guardam data,
quantidade de tentativas e a última mensagem devolvida pelo servidor.
Os itens adicionados permanecem no carrinho mesmo quando uma nova busca de
produtos substitui a lista de resultados. Rascunhos e pedidos pendentes são
isolados também por usuário, além de empresa e filial.

## API utilizada

Os endpoints ficam sob `/api/v1/mobile/`. Requisições operacionais enviam
`Authorization: Bearer`, `X-Tenant-Alias` e `X-Filial-ID`. Criação de pedido e
conclusão da separação também exigem `Idempotency-Key`.

O aplicativo não emite documento fiscal. Depois da separação, faturamento,
financeiro e eventual emissão fiscal continuam nos fluxos do ERP web.
