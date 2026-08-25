# Deploy log

## 2026-08-25 - Transparencia da tarifa de saida

- Confirmado em producao que o PIX ORENDA criou a tarifa separada de R$ 0,50 na conta ORENDA.
- Corrigido o detalhe do pagamento para mostrar o valor destinado ao fornecedor, a tarifa e o debito bancario total.
- O saldo do titulo nao incorpora a tarifa bancaria, pois ela e uma despesa da empresa e nao uma amortizacao da divida com o fornecedor.

Registro simples de disparos de deploy quando o Railway precisa receber um
commit novo pelo webhook do GitHub.

## 2026-05-20

- Motivo: Railway voltou apos outage parcial e o app publico ainda respondia
  `/health/` com 404, indicando deploy antigo.
- Base enviada: `488d653 Aprimora cadastro XML com lote e validade`.
- Escopo: consolidado do Thiago + estoque, entrada XML, fiscal/manifesto,
  resiliencia e preparacao Supabase/Cloudflare.
- Observacao: migrations e `ensure_quality_schema` rodam no `CMD` do Dockerfile.

## 2026-05-21

- Motivo: fechamento de estabilizacao do estoque MVP e integracao das mudancas paralelas de parametros/identidade da filial.
- Base enviada: `3aee713 Fecha estabilizacao do estoque MVP`.
- Railway: deploy `e43a9aa1-aef2-4212-a753-a2aa9aa1ba03` finalizado com `SUCCESS`.
- Migrations aplicadas em producao:
  - `core.0015_rename_registros_a_modulo_5a598c_idx_registros_a_modulo_a33783_idx_and_more`
  - `fiscal.0006_rename_regras_fisc_uf_aa0e88_idx_regras_fisc_uf_390d95_idx_and_more`
- Validacao local antes do deploy: 213 testes passaram.
- QA visual pos-deploy: dashboard, estoque, reposicao, movimentacoes, lotes, inventarios, entradas, produtos e promocoes abriram sem erro 500 e sem erros de console capturados.

## 2026-08-24

- Motivo: fechamento dos ajustes de posicao diaria, previsoes e tarifas de saida.
- Base enviada: `570dd5b1 feat: add payable forecasts and enforce outgoing fees`.
- Railway: deploy `6ced0330-f685-4fc1-8e90-d81699fab056` finalizado com `SUCCESS`.
- Migration aplicada em producao: `financeiro.0049_reforcar_tarifas_saida_orenda`.
- Escopo: recebimentos previstos e pagamentos previstos acima dos saldos, filtro padrao `Hoje`, acesso direto a quitacao e reforco das tarifas de saida PIX ORENDA/boleto em R$ 0,50.
- Validacao local: 40 testes passaram em `apps.financeiro.tests.test_posicao_diaria` e `apps.financeiro.tests.test_formas_pagamento`.
- Validacao HTTP: `https://ited.app.br` respondeu `302` para `/auth/login/`, esperado sem sessao autenticada.
- Aviso conhecido sem relacao com esta entrega: integracao IBPT registrou resposta 406 durante inicializacao.
