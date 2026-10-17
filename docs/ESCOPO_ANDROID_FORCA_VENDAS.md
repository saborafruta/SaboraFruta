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
