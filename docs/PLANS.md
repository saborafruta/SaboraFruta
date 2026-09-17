# PLANS.md

## Em andamento

### Compras / Entradas XML
- Fluxo atual de entrada:
  1. Vinculacao dos itens;
  2. Custos;
  3. Financeiro;
  4. Preço de venda.
- Concluído na sessão de 29/05/2026:
  - tipo de entrada e origem na importação XML;
  - comportamento da entrada com chips editáveis de estoque, financeiro e custo;
  - textos claros para `Estoque: Não`, `Financeiro: Não` e `Alterar custo: Não`;
  - tela de continuar entrada permitindo revisar comportamento antes da conferência;
  - financeiro da entrada com valor financeiro considerado, acréscimo/desconto em valor e percentual, rateio por categoria/subcategoria/tipo de despesa e centro de custo;
  - parcelas editáveis com vencimento, valor, forma de pagamento e observação;
  - formas de pagamento vindas do Financeiro por filial;
  - replicação separada de forma de pagamento e observação nas parcelas;
  - permissão do financeiro da entrada exigindo `compras/editar` + `financeiro/criar` para alterações.
- Regra definitiva: devolução de cliente não é entrada de compra/XML. Deve virar ajuste/estorno futuro, não tipo de entrada.
- A capa da NF deve diferenciar entrada iniciada de entrada finalizada.
- Entrada iniciada e nao finalizada deve mostrar mensagem clara no topo e acao para continuar.
- Enquanto a nota nao estiver finalizada, nao exibir listagem de itens recebidos na capa como se o recebimento estivesse concluido.
- Conferencia de itens deve permitir:
  - buscar produto interno por ID, nome, codigo/referencia e codigo de barras;
  - cadastrar produto a partir do item;
  - editar produto interno em sobreposicao;
  - remover vinculo do item por `x` pequeno;
  - ajustar conversao, lote e validade;
  - adicionar item manual quando necessario.
- Produto sem vinculo:
  - deve gerar alerta contextual grande ao tentar avancar;
  - pode prosseguir para custos somente se o usuario escolher `Prosseguir e vincular mais tarde`;
  - continua impedindo finalizacao/efetivacao enquanto nao for vinculado ou removido.
- Desvinculo manual:
  - nao deve revincular imediatamente pela mesma equivalencia antiga;
  - pode revincular por EAN se o produto for editado depois e o EAN passar a bater com o item da nota.
- Edicao de produto a partir da conferencia:
  - deve abrir em popup/sobreposicao;
  - deve notificar a conferencia ao salvar;
  - precisa preservar dados digitados mesmo quando campos de outra aba impedirem salvamento.
- Pendente:
  - QA visual final do financeiro da entrada em tema claro/escuro;
  - validar o datepicker da nova parcela e o botão compacto `+`;
  - decidir se rateio financeiro parcial apenas alerta ou bloqueia avançar;
  - validar fluxo real com entradas de compra para revenda, uso/consumo, ativo imobilizado, bonificação/amostra e consignação;
  - validar mobile da conferencia com os tres icones do produto interno;
  - melhorar mensagens de erro do cadastro de produto por aba/campo;
  - desenhar e implementar etapa 4 `Preço de venda`;
  - criar QA browser quando houver infraestrutura apropriada.

### PDV e Sugestão de compras
- O plano vivo de operação offline, queda de energia e contingência fiscal está em
  [`PDV_OFFLINE_E_CONTINGENCIA_FISCAL.md`](PDV_OFFLINE_E_CONTINGENCIA_FISCAL.md).
- Concluído na sessão de 29/05/2026:
  - abertura de caixa corrigida quando a filial não possui caixa ativo;
  - endpoint `POST /pdv/api/caixa/criar/` criando o próximo caixa ativo da filial;
  - botão `Abrir Caixa` condicionado a caixa selecionado;
  - mensagem clara para filial sem caixa cadastrado;
  - formas de pagamento do PDV integradas ao Financeiro por filial;
  - tela existente de reposição reaproveitada como `Sugestão de compras`;
  - menu desktop e mobile apontando para `/estoque/reposicao/`;
  - estado vazio da sugestão de compras explicado em linguagem operacional;
  - integração manual das atualizações do Thiago, preservando melhorias locais.
- Pendente:
  - QA real no Railway para filial sem caixa;
  - revisar PDV claro e escuro depois de novos ajustes visuais;
  - testar drawer de `Mais opções` e `Vendas pendentes` em desktop e mobile;
  - validar que formas de pagamento inativas não aparecem em venda nova;
  - completar fluxo final de recebimento/venda com formas de pagamento reais.

### Produtos
- listagem
- filtros
- paginacao
- imagens
- categorias
- subcategorias
- unidades
- marcas / fabricantes
- fornecedores vinculados
- replicacao por filial
- log completo de alteracoes
- combos e promocoes:
  - visao inicial com condicoes ativas
  - filtros por Todos, Ativas, Programadas, Finalizadas e por tipo
  - combo por quantidade
  - combo por quantidade pode usar o melhor preco vivo do produto como base, comparando preco promocional individual e desconto por categoria, com alternancia para preco original no cadastro/edicao
  - combo e kit so puxam automaticamente promocoes/descontos de categoria com pelo menos 5 dias da semana selecionados; abaixo disso a promocao e esporadica e nao deve entrar automaticamente
  - combo por quantidade com condicao `Quantidade` e `A partir de`; `A partir de` significa maior ou igual
  - kit de produtos
  - kit de produtos com ordem logica: nome, itens, revisao financeira, vigencia/dias/status/acoes
  - kit de produtos pode usar o melhor preco vivo dos itens quando marcado
  - brinde por produto
  - brinde por produto usa produto gerador de brinde, quantidade minima e um ou mais itens gratuitos, com baixa futura de estoque no PDV
  - brinde por produto pode usar o melhor preco vivo do produto gerador quando marcado, seguindo a mesma regra de minimo de 5 dias da semana
  - desconto por categoria/subcategoria
  - precos promocionais em lote
  - formularios minimizados acima e listagens abaixo
  - busca de produto por ID, codigo/referencia, codigo de barras e nome
  - layout mobile e desktop em refinamento continuo
  - calendario de vigencia precisa navegar meses, limpar datas e salvar vazio em criacao/edicao
  - dias da semana podem limitar quando combo, kit, desconto por categoria e preco promocional em lote funcionam; padrao todos os dias
  - status Programado, Ativo, Finalizada e Inativo devem aparecer corretamente nas listagens/filtros
  - replicacao de promocoes e seletiva por filial destino, com filial bloqueada exibindo motivo antes do salvamento
  - copias replicadas de combos, kits, brindes e descontos por categoria viram independentes depois de criadas
  - preco promocional individual fica no fluxo de promocoes; cadastro do produto mantem apenas o preco de venda base
  - etapa de combos e promocoes considerada concluida em 18/05/2026; proximas alteracoes devem ser corretivas, PDV ou evolucoes planejadas

### Qualidade
- padroes por categoria/subcategoria.
- parametros especificos por produto.
- abrir pela lista de produtos deve priorizar configuracao por categoria/subcategoria.
- busca de produto precisa ser por ID, nome, codigo/referencia e codigo de barras.

### Ficha tecnica / composicao
- existe como area futura para composicao/BOM.
- consumos internos e insumos do produto devem entrar aqui, nao em combos/promocoes.
- deve ser generica para industria e refinada por segmento com o tempo.

### Auditoria
- produto usa log especifico.
- cadastros e central usam log generico.
- todo log de cadastro deve mostrar:
  - quando
  - quem
  - o que fez
  - quanto alterou
  - detalhe
  - campo
  - antes
  - depois
- normalizar numeros reais para evitar ruido.
- nao normalizar CPF/CNPJ/CEP/codigos como decimal.

## Proximos modulos
1. estoque - MVP consolidado em 21/05/2026; proximas mudancas devem ser corretivas, acabamento operacional ou integracao com compras/vendas/PDV
2. producao
3. fiscal
4. financeiro

## Estoque - inicio
- Estoque e operacional por filial e nao deve ser replicado.
- Saldo, reserva, lote, movimento, inventario e alerta pertencem a filial fisica onde ocorrem.
- Transferencia entre filiais nao e replicacao: deve gerar saida na filial origem e entrada na filial destino, na mesma transacao.
- Toda alteracao de saldo deve passar por `MovimentacaoService`; nunca escrever diretamente em `Estoque.quantidade_atual`.
- Historico de estoque deve guardar quantidade anterior e posterior, usuario, data, produto, filial, documento e observacao.
- Primeira etapa implementada/organizada:
  - tela de saldo com KPIs;
  - tela de saldo lista todos os produtos ativos da filial, mesmo sem movimentacao, com saldo zero;
  - busca por ID, codigo/referencia, codigo de barras e nome;
  - atalho de movimentacao por produto na listagem de estoque;
  - nova movimentacao manual;
  - ajuste manual por quantidade fisica real;
  - historico de movimentacoes;
  - transferencia entre filiais como movimento bilateral.
  - criacao de lote com quantidade inicial gera entrada pelo service de estoque;
  - quantidade inicial do lote fica bloqueada apos criacao, mantendo ajustes no fluxo auditado de movimentacao.
  - inventario basico abre snapshot da filial, salva contagens e fecha gerando ajustes auditados;
  - inventario pode bloquear movimentacoes comuns da filial enquanto estiver aberto.
  - servicos legados de custo medio e FIFO/FEFO delegam saldo para `MovimentacaoService`.
  - alertas de estoque agora unem vencimento de lotes e estoque minimo na mesma tela operacional.
  - alerta de estoque minimo considera produto ativo vinculado a filial mesmo quando ainda nao existe linha em `Estoque`, tratando saldo como zero.
  - tasks de alerta nao replicam saldo; apenas contam/sinalizam riscos por produto-filial.
  - reserva e liberacao de estoque para pedidos de venda foram centralizadas em `MovimentacaoService`.
  - `MovimentacaoService` valida que lote informado pertence ao produto e filial da operacao antes de alterar quantidades.
  - baixa por validade foi adicionada como saida auditada especifica para lote vencido, sem liberar uso de lote vencido em venda normal.
  - saida FEFO agora faz baixa direta no saldo consolidado quando o produto nao controla lote.
  - reserva de produto com controle de lote valida lotes vigentes via FEFO antes de confirmar disponibilidade.
  - paginacao das telas de estoque, lotes, movimentacoes e inventarios preserva os filtros aplicados.
  - lista de estoque ganhou atalho para extrato do produto usando filtro exato na tela de movimentacoes.
  - consultas de saldo, lotes e movimentacoes permitem exportar CSV respeitando os filtros aplicados.
  - historico de movimentacoes permite filtrar por periodo antes de consultar ou exportar CSV.
  - listagem de lotes vencidos exibe dias vencidos como numero positivo.
  - comando `conferir_estoque` confere saldo consolidado, disponibilidade, ultimo snapshot e total de lotes; por padrao nao altera dados.
  - movimentacoes de produto que controla lote agora exigem `lote_id`; devolucao de venda tenta retornar pelo lote das saidas faturadas.
  - cadastro de produto nao ajusta saldo direto quando o produto controla lote; deve usar lote/movimentacao com lote identificado.
  - lista de estoque calcula sugestao de reposicao usando estoque maximo, ponto de reposicao ou estoque minimo, nessa ordem.
  - inventario com bloqueio ativo tambem impede nova reserva de estoque, evitando venda confirmada durante contagem fisica.
  - testes iniciais de `MovimentacaoService` cobrem lote obrigatorio, lote por filial, FEFO, reserva e bloqueio por inventario.
  - `config.settings.test` permite rodar testes locais em SQLite sem depender das migrations Postgres antigas.
  - transferencia entre filiais exige produto ativo/vinculado tambem na filial destino para evitar estoque invisivel.
  - transferencias geram o mesmo documento `TRF-...` na saida e na entrada, com referencia cruzada entre as movimentacoes.
  - entrada em lote esgotado reativa o lote quando volta a ter saldo positivo e nao esta vencido.
  - detalhe do inventario ganhou resumo de progresso, itens contados/pendentes, divergencias, sobras e faltas valorizadas.
  - detalhe do inventario permite exportar CSV da contagem com sistema, contado, diferenca, valor e justificativa.
  - lista e CSV de movimentacoes exibem melhor rastreio de transferencia, com origem/destino e movimento relacionado.
  - busca de movimentacoes tambem encontra movimento/documento por ID e filial de destino.
  - suite de estoque subiu para 11 testes, incluindo resumo de inventario.

## Fechamento da etapa Combos e Promocoes - 18/05/2026

### Concluido
- Tela unificada `Produtos > Combos e Promocoes` com abas:
  - Ativos
  - Combo
  - Kit
  - Brindes
  - Desconto por categoria
  - Precos promocionais
- Combo por quantidade do mesmo produto:
  - nome do combo;
  - produto principal;
  - faixas com `Quantidade` ou `A partir de`;
  - tipo de desconto em percentual, valor ou preco unitario/final conforme regra existente;
  - vigencia opcional;
  - dias da semana;
  - status;
  - replicacao seletiva;
  - listagem com editar/inativar.
- Kit de produtos:
  - produtos diferentes;
  - quantidade por item;
  - calculo de total sem kit, desconto e total do kit;
  - opcao de usar melhor preco vivo dos itens;
  - listagem e edicao por clique/toque.
- Brindes:
  - nova aba;
  - produto gerador de brinde;
  - quantidade minima para ganhar;
  - um ou mais itens gratuitos;
  - exibicao `Gratis` no PDV previsto, em vez de mostrar desconto monetario para o brinde;
  - resumo visual e listagem com item vendido, brindes, validade, status e acoes;
  - baixa futura de estoque no PDV.
- Desconto por categoria/subcategoria:
  - nome do desconto;
  - categoria ou todas as categorias;
  - subcategoria opcional;
  - desconto por percentual ou valor;
  - uso opcional de promocao individual como base, com aviso de desconto sobre desconto;
  - vigencia, dias da semana, status e replicacao.
- Precos promocionais:
  - movidos para a tela de promocoes;
  - cadastro em lote por produto;
  - desconto percentual, desconto em valor e preco promocional se recalculam entre si;
  - campos monetarios limitados a 2 casas decimais;
  - listagem com dias da semana, status, editar e inativar.
- Auditoria:
  - botao `Log`;
  - log de criacao, edicao, inativacao e alteracoes relevantes;
  - dias da semana exibidos como nomes, nao numeros;
  - log protegido para nao derrubar a tela principal em caso de dado inconsistente.
- Mobile:
  - formularios e listagens revisados para mobile;
  - listagens clicaveis/editaveis no padrao de produto;
  - grids quebram em coluna unica quando necessario.
- Temas:
  - tema claro segue branco/cinza claro com laranja;
  - tema escuro segue preto/cinza escuro com azul como destaque principal;
  - tooltips de origem promocional deixaram de usar tooltip preto nativo e passaram a usar balao visual proprio.

### Regras finais de preco vivo
- Preco vivo comercial compara:
  - preco de venda;
  - preco promocional individual;
  - desconto por categoria/subcategoria.
- O sistema usa o menor preco elegivel quando a flag de uso de preco promocional/melhor preco esta marcada.
- Nao acumular descontos paralelos por padrao; comparar candidatos e escolher o menor.
- Em combo, kit e brinde, o uso automatico de promocao externa exige:
  - promocao ativa;
  - dentro da vigencia;
  - pelo menos 5 dias da semana selecionados.
- Promocoes com 1 a 4 dias da semana sao consideradas esporadicas e nao entram automaticamente em combo/kit/brinde.
- No PDV futuro:
  - mostrar todas as opcoes elegiveis em modal;
  - sugerir a mais barata;
  - nao aplicar automaticamente quando houver varias opcoes sensiveis.

### Pendente
- Integrar essas regras ao PDV real.
- No PDV, exibir combos, kits, brindes, descontos por categoria, precos promocionais e futuras campanhas CRM.
- No PDV, deixar o vendedor escolher a promocao quando houver mais de uma opcao.
- Baixa de estoque real de kits e brindes no PDV.
- Testes de ponta a ponta em producao com duas ou mais filiais reais.
- Futuro modulo de CRM para campanhas por cliente/grupo de clientes.
- Proximo modulo principal: estoque, dentro do projeto inicial de unificacao do Thiago.

## Ideias futuras ja combinadas
- PDV deve futuramente mostrar combos, kits, descontos por categoria e preco promocional quando o vendedor selecionar um produto.
- No PDV, quando houver mais de um preco aplicavel, exibir as opcoes em modal, sugerir o menor preco, mas deixar o vendedor selecionar; nao inserir automaticamente.
- Em kits, cliente visualiza os itens e estoque baixa item por item.
- Em combo do mesmo produto, vendedor escolhe a variacao/faixa aplicavel.
- No combo do mesmo produto, regra `Quantidade` vale para quantidade exata; regra `A partir de` vale para quantidade maior ou igual. No PDV futuro, a escolha da melhor faixa precisa respeitar essa diferenca.
- No PDV, brindes devem aparecer como item gratuito vinculado a promocao, com sugestao clara para o vendedor e baixa de estoque do produto entregue.
- Pode haver flag futura para permitir ou nao combinar preco promocional com combo/kit.
- Replicacao de combos/kits/brindes/descontos/precos promocionais ja permite escolher filiais destino individualmente.
- Importacao por Excel de produtos foi citada como desejo futuro, mas ainda nao iniciada.

## Fechamento parcial da etapa Estoque - 18/05/2026

### Concluido
- Estoque permanece 100% por filial e nao replicado. Transferencia continua sendo movimento bilateral auditado, nao copia de saldo.
- Permissoes refinadas:
  - ajustar, transferir e inventariar/fechar usam `estoque:editar`;
  - criar lote/inventario usa `estoque:criar`;
  - baixar validade e cancelar inventario usam `estoque:cancelar`;
  - CSV usa `estoque:exportar`;
  - gerar reposicao em pedido exige `estoque:editar` e `compras:criar`.
- Criada tela de reposicao em `/estoque/reposicao/`:
  - lista sugestoes reais por minimo/ponto/maximo;
  - mostra totais, itens com/sem fornecedor e quantidade sugerida;
  - gera pedidos de compra em rascunho agrupados por fornecedor;
  - exporta CSV somente com permissao de exportacao.
- Criado relatorio de divergencias de inventario em `/estoque/inventarios/<id>/divergencias/`:
  - atalhos a partir da lista e detalhe de inventario;
  - resumo de divergencias, sobras, faltas e liquido;
  - cards mobile e tabela desktop;
  - exportacao CSV protegida.
- Mobile das telas densas foi reforcado:
  - base de listagens com largura minima e rolagem horizontal;
  - reposicao e divergencias com cards mobile;
  - paginacao comum agora quebra melhor em telas pequenas.
- Criados testes adicionais de forms/views:
  - validacao de lote obrigatorio para produto controlado;
  - transferencia rejeitando lote de outro produto;
  - exportacao bloqueada/permitida por permissao;
  - reposicao gerando pedido de compra em rascunho;
  - tela de divergencias carregando com permissao de leitura.
- Validacoes locais:
  - `python manage.py test apps.estoque.tests --settings=config.settings.test` passou com 18 testes;
  - `python manage.py check --settings=config.settings.development` passou usando SQLite local;
  - templates principais do estoque foram carregados sem erro.
- Validacoes em producao/Railway:
  - diretorio vinculado ao projeto `zucchini-renewal`, ambiente `production`, servico `erp-inoovated`;
  - deploy ativo no commit `3519c03`, status `SUCCESS`, com 1 replica rodando;
  - `python manage.py check` contra o Postgres publico passou;
  - HTTP publico: login 200; `/estoque/` e `/estoque/reposicao/` 302 para login, sem 500;
  - logs recentes sem Traceback/Internal Server Error/HTTP 500;
  - telas saldo, reposicao, movimentacoes, lotes e inventarios renderizaram 200 para Filial 1 e Filial 2 usando dados reais.
- Snapshot de producao no teste:
  - 3 filiais ativas;
  - Filial 1 `Polpa do Nordeste - Matriz Natal`: 3 produtos vinculados, 3 saldos e 4 movimentacoes;
  - Filial 2 `Polpa do Nordeste - Mossoro`: 3 produtos vinculados, sem saldos/movimentacoes;
  - Filial 3 `Fiber - SP`: sem produtos vinculados.

### QA ampliado de estoque - 18/05/2026
- Compras e vendas foram tratados como modulos inacabados; o QA cobriu somente os contratos ja relacionados a estoque.
- Testes novos cobrem:
  - entrada de compra efetivada criando lote, saldo e movimento;
  - venda confirmada reservando estoque;
  - cancelamento liberando reserva;
  - faturamento liberando reserva e baixando saldo;
  - cancelamento em separacao liberando reserva;
  - venda usando preco promocional sem alterar saldo, reserva ou custo medio;
  - contexto de replicacao de promocoes usando nome exibivel da filial.
- Correcao funcional:
  - `PedidoVenda.pode_cancelar` agora permite cancelar `em_separacao`, porque o service ja libera reserva nesse status.
- Correcao de produtos/promocoes:
  - tela de promocoes deixou de usar `Filial.nome` e passou a usar `nome_fantasia`/`razao_social`, removendo erro de log em producao.
- Validacoes locais:
  - `python manage.py test apps.estoque.tests apps.produtos.tests --settings=config.settings.test` passou com 52 testes;
  - `python manage.py check --settings=config.settings.development` passou;
  - templates principais de estoque e promocoes carregaram sem erro.
- Validacao read-only contra Postgres de producao:
  - saldo, reposicao, movimentacoes, lotes, inventarios e promocoes renderizaram 200 para Filial 1 e Filial 2.

### QA real com rollback - 19/05/2026
- O banco Railway e de teste, mas o QA foi executado com rollback transacional para nao deixar registros temporarios.
- Antes do QA foi corrigida compatibilidade com colunas obrigatorias legadas do banco real:
  - `compras.0002_compat_legacy_required_fields`;
  - `vendas.0002_compat_legacy_required_fields`.
- Escopo mantido: compras e vendas seguem inacabados; foi validado apenas o contrato que afeta estoque.
- Fluxos reais validados contra as filiais `Polpa do Nordeste - Matriz Natal` e `Polpa do Nordeste - Mossoro`:
  - entrada com custo medio;
  - venda com preco promocional reservando e faturando estoque sem alterar custo medio;
  - transferencia entre filiais como movimentacao bilateral, nao replicacao;
  - compra com lote e validade;
  - baixa de lote vencido;
  - inventario fechado gerando ajuste auditado;
  - reposicao gerando pedido de compra em rascunho;
  - `conferir_estoque` sem divergencias para os produtos QA;
  - telas saldo, reposicao, movimentacoes, lotes, inventarios e promocoes renderizando 200 nas duas filiais.
- Dados criados dentro da transacao: 5 produtos, 1 fornecedor, 1 cliente, 5 movimentos, 1 pedido de compra e 1 inventario.
- Conferencia apos rollback: produtos=0, fornecedores=0, clientes=0, movimentos=0, pedidos_compra=0, inventarios=0 para o prefixo `QA ESTOQUE`.

## Entrada de Mercadoria / Manifesto - fase 1 iniciada em 19/05/2026

### Concluido nesta fase
- Base de Entrada de NF ampliada para recebimento fiscal/operacional em `apps/compras`, mantendo compras como modulo ainda inacabado.
- Novas origens de entrada: manual, XML, chave de acesso e manifesto fiscal.
- Importacao XML cria entrada com XML original, chave, emitente, destinatario, totais e itens.
- Regra definida pelo usuario: nota de qualquer CPF/CNPJ deve entrar. Documento do destinatario diferente da filial e apenas alerta, nao bloqueio.
- Fornecedor desconhecido no upload XML agora e cadastrado automaticamente quando o XML traz documento e razao social do emitente.
- Fornecedor tecnico pendente fica como fallback para entrada por chave/manual ou XML sem dados suficientes; a tela permite criar pelo XML, vincular existente ou manter pendente, migrando equivalencias por CNPJ XML quando resolvido.
- Produto sem equivalencia fica como diferenca bloqueante e impede finalizacao.
- Equivalencias por EAN/codigo fornecedor foram criadas para acelerar proximas entradas.
- Conferencia de itens agora sugere produtos por nome parecido, NCM e unidade quando EAN/equivalencia nao resolvem; o operador confirma manualmente o vinculo.
- A confirmacao em massa da conferencia permite escolher entre sugestoes recalculadas no servidor, ajustar fator/unidade/lote/validade e ignora produto que nao pertence as sugestoes seguras do item.
- Item sem produto pode cadastrar produto pelo XML, ja criando EAN/equivalencia e vinculando ao item; o produto nasce com observacao para revisar cadastro fiscal/comercial antes da venda.
- Cadastro rapido pelo XML herda controle de lote/validade quando o item veio com rastro e reaproveita produto ja criado por EAN/equivalencia em NF com multiplos lotes, evitando duplicidade.
- Fator de conversao foi implementado para quantidade e custo unitario por unidade de estoque.
- Parcelas/faturas do XML entram como pre-lancamento financeiro pendente; contas a pagar continuam sendo geradas apenas por acao manual apos efetivar a entrada.
- Telas de diferencas e finalizacao recalculam lote/validade/quantidade antes de exibir bloqueios, evitando liberar visualmente uma entrada com flags antigas.
- Finalizacao de entrada continua passando por `MovimentacaoService`.
- Manifesto Fiscal ganhou models, services locais e telas base para config/documentos/logs.
- Documento do Manifesto com `xml_completo` agora pode virar Entrada de NF pelo botao `Importar entrada`; o fluxo valida se a chave do XML pertence ao manifesto, reaproveita o importador XML, cadastra fornecedor automaticamente quando houver dados do emitente e vincula uma entrada ja existente pela mesma chave sem duplicar.
- A lista do Manifesto ganhou acoes de `Abrir entrada`/`Importar entrada` e versao mobile em cards para evitar overflow da chave de acesso.
- Manifesto sem XML completo ganhou acao `Anexar XML`, com tela para upload ou XML colado. O XML e salvo somente se a chave interna da NF-e bater com a chave do manifesto; tambem ha acao `Salvar e importar entrada`.
- A consulta DF-e agora passa por `DFeClient` seguro. O modo padrao `local` nao acessa SEFAZ, nao usa certificado e nao cria documentos falsos; o modo `sefaz` consulta `NFeDistribuicaoDFe` apenas com flag real ligada, certificado A1 valido e senha em ambiente.
- Manifestacoes fiscais reais continuam bloqueadas. `Dar ciencia`, `desconhecer` e `nao realizada` registram apenas estado local/log no ERP nesta etapa.
- Configuracao DF-e ganhou painel de prontidao: mostra modo local/SEFAZ, flag de consulta real, certificado A1, senha via `FISCAL_DFE_CERT_PASSWORD` e eventos reais, sem expor segredo nem acionar SEFAZ.
- Cliente SEFAZ bloqueia consulta real por etapas: flag desligada, certificado ausente, senha ausente, A1 invalido, producao nao liberada e cooldown quando `ultimo_nsu` ja alcancou `max_nsu`.
- Testes cobrem XML com documento diferente, duplicidade de chave, EAN/fator/custo/estoque, sugestao por nome/NCM, cadastro rapido de produto pelo item, bloqueio por item sem produto e renderizacao basica das telas de localizar/importar/conferencia.
- Testes adicionais cobrem importacao de manifesto para entrada, bloqueio de manifesto sem XML, vinculo com entrada existente e recusa de XML de outra chave.
- Testes do anexo de XML cobrem renderizacao da tela, salvamento local, recusa de chave divergente, botao na lista e salvar+importar para conferencia.
- Testes de seguranca DF-e cobrem consulta local vazia, bloqueio de SEFAZ real por padrao, bloqueio de eventos reais, sync por client fake e preservacao de documento ja importado.
- `xmls teste.zip` validado em 19/05/2026: localmente 10 NF-e importaram com rollback, 1 XML invalido foi rejeitado e 1 chave duplicada foi bloqueada; apos fornecedor automatico, as 10 NF-e validas criaram 10 fornecedores unicos na transacao e nenhuma ficou com fornecedor pendente; no Railway/Postgres, amostra real rodou em 2 filiais com rollback confirmado e sem deixar entradas criadas.

### Pendente planejado
- Testar pela tela o upload do certificado A1 no Railway e a consulta DF-e em homologacao com NF-e destinada ao CNPJ configurado.
- Fazer QA visual pos-deploy no Railway para upload XML com fornecedor automatico e para o fallback de fornecedor pendente, limpando qualquer dado criado.

## Cuidados permanentes
- replicacao por filial
- mobile-first
- tema claro branco/laranja
- tema escuro preto/azul
- logs sem ruido
- deploy Railway sem 500 por sincronizacao opcional
- precos sempre com 2 casas decimais
- quantidades sem zeros desnecessarios quando forem inteiras
- layout alinhado e visualmente polido
- botoes principais solidos e coerentes com o tema
- calendarios funcionando em navegacao de meses, limpeza e salvamento vazio
- dias da semana de combos/promocoes preservados em edicao, listagem e replicacao
- nao misturar insumos internos com promocao comercial
