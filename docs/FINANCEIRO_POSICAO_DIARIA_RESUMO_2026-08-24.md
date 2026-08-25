# Financeiro e posicao diaria - resumo tecnico

Documento de encerramento da rodada de ajustes concluida em 24/08/2026.

## Tarifas ao pagar

As tarifas de saida sao lancadas separadamente como despesa bancaria. No detalhe de cada pagamento sao apresentados o valor pago ao fornecedor, a tarifa aplicada e o total debitado da conta bancaria. A tarifa nao reduz o saldo devido ao fornecedor.

## Estado em producao

- Dominio: `https://ited.app.br`.
- Branch de trabalho: `codex/posicao-diaria-ajustes`.
- Ultimo commit funcional implantado: `570dd5b1 feat: add payable forecasts and enforce outgoing fees`.
- Deploy Railway: `6ced0330-f685-4fc1-8e90-d81699fab056`, status `SUCCESS`.
- Migration confirmada no PostgreSQL de producao: `financeiro.0049_reforcar_tarifas_saida_orenda`.
- Validacao automatizada: 40 testes passaram em `apps.financeiro.tests.test_posicao_diaria` e `apps.financeiro.tests.test_formas_pagamento`.
- O dominio respondeu `302` para `/auth/login/`, comportamento esperado para acesso sem sessao.

## Posicao diaria

- Entradas e saidas ficam lado a lado, com verde para entradas e vermelho para saidas.
- Movimentos usam horario mais recente primeiro e podem ser agrupados por conta bancaria ou forma de pagamento.
- O antigo extrato consolidado do dia foi removido.
- Conferencia mostra entradas por forma, entradas por conta, saidas por forma e saidas por conta.
- Cards de saldo por conta aparecem depois das previsoes.
- O controle com icone de olho oculta/desfoca todos os valores financeiros da tela.
- No tema claro, valores e alertas devem manter contraste forte. No mobile, a tela deve priorizar o fluxo principal e compactar informacoes secundarias.
- Categorias exibidas em cards e modais mostram apenas a categoria final; a arvore completa permanece nos formularios de edicao.

## Entradas e recebimentos

- Entradas manuais possuem classificacao de receita guiada em tres niveis na criacao e edicao.
- Receitas padrao incluem vendas, contas a receber, emprestimos, aportes, rendimentos, estornos e outras receitas.
- Vendas, recebimentos de contas a receber e recebimentos de OP devem entrar no caixa quando efetivamente liquidados.
- Quando a forma possui prazo de compensacao, o movimento aparece na data prevista de credito, nao na data da venda.
- Entradas manuais preservam a data selecionada e podem ser editadas com historico de alteracoes.
- Recebimentos vencidos ficam destacados em vermelho claro e podem ser renegociados, registrando data anterior, nova data, motivo e historico.

## Previsoes

- `Recebimentos previstos` fica acima de `Saldos por conta`.
- `Pagamentos previstos` usa o mesmo padrao e fica logo abaixo dos recebimentos e antes dos saldos.
- Os dois blocos iniciam no filtro `Hoje`.
- Filtros disponiveis: Hoje, 7 dias, 15 dias, 30 dias, mes atual e personalizado.
- Os blocos mostram primeiro apenas o total e usam `Ver mais`/`Ocultar` para abrir a listagem.
- Uma conta a pagar prevista e clicavel e abre diretamente o fluxo de quitacao.

## Formas de pagamento, contas e taxas

- Cada forma pode ter uma conta bancaria padrao. Essa conta deve aparecer junto ao nome da forma, vir pre-selecionada e continuar editavel.
- Formas da maquininha ORENDA sao direcionadas para a conta ORENDA.
- Bandeira e parcelas sao opcionais e editaveis. Esses campos aparecem somente para cartao.
- Cartao de debito nao possui parcelas. Cartao de credito respeita o numero maximo de parcelas cadastrado.
- Taxas de entrada reduzem o valor antes do credito bancario: valor liquido = valor bruto - taxa percentual - taxa fixa.
- O card da entrada mostra valor liquido em destaque e, como apoio, valor original e taxa em reais.
- A taxa de entrada nao deve gerar uma segunda saida bancaria, pois ja foi retida antes da liquidacao.
- Taxas de saida sao despesas bancarias reais e adicionais ao pagamento.
- Regras ORENDA reforcadas em producao: pagar com `PIX (ORENDA)` cobra R$ 0,50; pagar boleto cobra R$ 0,50.
- Receber em `PIX (ORENDA)` cobra 0,99%; receber boleto cobra R$ 4,50, conforme configuracao da forma.
- Taxas devem ser classificadas contabilmente como despesa bancaria, sem duplicar impacto no saldo.
- Na interface, usar somente o nome `Taxa`; evitar `Taxas descontadas nas entradas` e titulos longos.

## Contas a pagar

- Formas de pagamento exibem a conta vinculada e preenchem automaticamente a conta debitada, sem trava para alteracao.
- Quitacao permite integral, parcial e atalho `50% do saldo`.
- Juros, multa e desconto alteram o valor final uma unica vez; o pagamento e o saldo devem refletir esse valor sem duplicacao.
- Recorrencias aceitam diaria, semanal, mensal, anual e intervalo personalizado em dias.
- Regras mensais disponiveis: primeiro dia do mes, ultimo dia do mes, dia X do mes e quinto dia util.
- Existem duas flags independentes e ambas podem ficar marcadas:
  - `Levar em conta apenas dias uteis`: se cair em dia nao util, usa o proximo dia util.
  - `Antecipar para o dia util anterior`: antecipa obrigacoes que nao podem passar do vencimento.
- Para despesas classificadas como imposto, a antecipacao para o dia util anterior deve ser sugerida por padrao, mantendo a possibilidade de desmarcar.
- A categoria final `Insumos` existe sob `Custos das Mercadorias Vendidas > Mercadorias e Insumos` e possui vinculo contabil.

## Contas pagas e analises

- Ordem da pagina: filtros e listagem, analise por categoria financeira, gasto por fornecedor e meta de despesas pessoais.
- A listagem inicial e limitada a 10 registros e informa quando existem mais resultados.
- Gasto por fornecedor respeita periodo e filtros de classificacao; clicar no fornecedor lista as contas correspondentes.
- A analise por categoria usa ranking, valor, percentual do total e impacto percentual no faturamento.
- A meta de despesas pessoais acompanha sempre o mes completo, mostra usado, meta, disponivel e barra de progresso.
- A meta pode ser valor fixo ou percentual do faturamento do mes anterior/media de meses anteriores.

## Historico e edicao

- Movimentos manuais de entrada e saida podem ser editados com os dados anteriores preenchidos.
- Edicao abrange valor, data, conta, forma, classificacao, documento e motivo.
- Contas a pagar/pagas e movimentos manuais exibem historico de alteracoes em formato legivel, resumindo somente os campos que mudaram.
- Nao mostrar IDs internos de categoria, receita ou despesa ao usuario final.

## Regras visuais permanentes

- Nao usar cabecalho local colorido ou em degradê dentro da pagina ou modal.
- Nao repetir titulo da pagina dentro do conteudo.
- Nao criar listagens com um card alto por registro quando uma linha compacta resolve.
- Nao usar vermelho ou verde claros demais no tema claro.
- Nao mostrar a arvore inteira da categoria em cards compactos.
- Nao criar secoes grandes e redundantes de taxas; o detalhamento deve ser compacto e orientado a bruto, taxa e liquido.
- Consultar `docs/UI_RULES.md` antes de qualquer nova tela ou alteracao visual.

## Pontos para validar em uma proxima rodada

- Fazer QA autenticado ponta a ponta de venda, OP paga e conta a receber, comparando data da operacao com data de liquidacao.
- Confirmar visualmente em producao o atalho de 50% e o preenchimento automatico da conta em todos os formularios de pagamento.
- Validar com uma quitacao real que juros/multa nao sejam somados duas vezes.
- Conferir uma saida PIX ORENDA e uma saida por boleto para confirmar a tarifa de R$ 0,50 no saldo bancario e no historico contabil.
- Fazer QA responsivo da posicao diaria no tema claro e escuro.

## Arquivos principais

- `apps/financeiro/views/posicao_diaria.py`
- `apps/financeiro/services/posicao_diaria_service.py`
- `apps/financeiro/templates/financeiro/posicao_diaria.html`
- `apps/financeiro/templates/financeiro/partials/previsoes_posicao_diaria.html`
- `apps/financeiro/templates/financeiro/pagar/form.html`
- `apps/financeiro/templates/financeiro/pagar/pagamento.html`
- `apps/financeiro/migrations/0046_contapagar_antecipar_vencimento_dia_util_and_more.py`
- `apps/financeiro/migrations/0047_contapagar_regra_vencimento_mensal.py`
- `apps/financeiro/migrations/0048_criar_categoria_insumos.py`
- `apps/financeiro/migrations/0049_reforcar_tarifas_saida_orenda.py`
