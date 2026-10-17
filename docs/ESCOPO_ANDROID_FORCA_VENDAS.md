# Aplicativo Android — ITED Força de Vendas

## Decisões confirmadas

- Aplicativo Android nativo, mantendo o padrão visual e operacional do ITED.
- Android mínimo 9 (API 28) e alvo Android 16 (API 36).
- Sem emissão fiscal.
- Sincronização de clientes, produtos, preços, pagamentos, depósitos, estoque,
  lotes, pedidos, status e permissões.
- Operação do vendedor e do estoquista no mesmo aplicativo, limitada pelas
  permissões do usuário.
- O banco online do ITED permanece como fonte oficial.

## Sincronização visível

O aplicativo deve manter um indicador permanente com os estados:

- sincronizado;
- sincronizando;
- operações pendentes;
- sem conexão;
- falha de sincronização.

A Central de sincronização deve mostrar empresa, filial, data e hora da última
sincronização, recursos atualizados, pedidos e separações pendentes e erros. O
usuário terá ações para sincronizar e repetir envios.

## Confirmação de pedido

Um pedido local percorre `rascunho`, `aguardando envio`, `enviando`, `recebido
pelo ITED` ou `falha`. Somente a resposta do servidor com ID, número oficial e
data de recebimento comprova a entrega. Cada operação usa uma chave idempotente.

## Separação

O estoquista seleciona pedido, produto, depósito, quantidade e, quando
aplicável, lote. O sistema sugere FEFO, aceita divisão entre lotes, impede lote
vencido/bloqueado/esgotado/em quarentena e revalida o saldo do lote no depósito
antes da baixa atômica. Produtos sem controle de lote não exigem seleção.

## Primeira previsão

- versão instalável: 12 a 15 dias úteis;
- piloto completo: 22 a 28 dias úteis;
- produção: após validação do piloto nos aparelhos reais.

## Marco técnico 0.2

Implementado no primeiro recorte vertical:

- autenticação JWT no diretório central e seleção de empresa/filial;
- contratos REST de clientes, produtos, preços, pagamentos e pedidos;
- envio idempotente de pedidos e confirmação pelo número oficial;
- cache local e fila de pedidos pendentes quando a internet falha;
- sincronização manual e periódica condicionada à disponibilidade de rede;
- consulta da fila de separação, depósitos, saldos e lotes vigentes;
- conferência de quantidade/depósito, escolha manual de lote ou FEFO e
  conclusão online idempotente;
- baixa de estoque reutilizando o `VendaService`, sem duplicar a regra do ERP.

Produtos controlados permitem escolher um lote vigente ou manter a sugestão
automática por FEFO. O servidor revalida status, validade, saldo global do lote
e saldo do depósito antes da baixa. O modelo atual ainda não distribui o saldo
de cada lote entre depósitos; essa granularidade é uma evolução futura e fica
explicitada para não sugerir uma precisão que o banco ainda não armazena.

## Marco técnico 0.3

Complementos implementados sobre o piloto:

- Central de sincronização com empresa, filial, última atualização, recursos
  disponíveis localmente e erros;
- histórico local de cada pedido pendente, incluindo criação, tentativas e
  última falha de envio;
- rascunho persistente por empresa/filial e carrinho independente do resultado
  atual da pesquisa de produtos, com isolamento adicional por usuário;
- repetição manual de todos os envios pela Central e repetição automática pelo
  WorkManager;
- cache de buscas isolado por termo para não misturar clientes ou produtos no
  uso offline;
- separação manual dividida entre dois ou mais lotes, mantendo FEFO como opção;
- validação local da soma e do saldo informado por lote antes do envio, seguida
  pela revalidação atômica no servidor.

Continuam fora do aplicativo a emissão fiscal e o faturamento. A precisão de
saldo de cada lote por depósito depende de uma futura evolução do modelo de
estoque; hoje o servidor valida separadamente o saldo do depósito e o saldo
global do lote.
