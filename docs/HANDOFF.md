# HANDOFF.md

## Correcao de tarifa em pagamentos (2026-08-25)

- Pagamentos de contas a pagar mantem o valor pago ao fornecedor separado da tarifa bancaria.
- A tarifa de saida configurada na forma de pagamento gera uma conta paga propria e vinculada a mesma conta bancaria.
- O detalhe do titulo agora exibe `Pago ao fornecedor`, `Tarifa bancaria` e `Total debitado da conta`.
- Exemplo: pagar R$ 500,00 por PIX ORENDA com tarifa de R$ 0,50 mantem R$ 494,99 de saldo no titulo de R$ 994,99 e debita R$ 500,50 da conta ORENDA.

## Estado atual
- multiempresa
- multifilial
- replicacao por filial
- clientes
- fornecedores
- produtos
- marcas / fabricantes
- categorias
- subcategorias
- unidades
- mobile
- temas
- logs de auditoria em cadastros

## Produtos
- Produto tem marca/fabricante opcional.
- Produto tem fornecedor opcional.
- Lista de produtos filtra por categoria, subcategoria, marca/fabricante e fornecedor.
- Tela de produto preserva imagem ao editar dados que nao sao imagem.
- Produto tem botoes/atalhos para log, ficha tecnica e qualidade na edicao.
- Lista de produtos tem botoes superiores na prioridade definida pelo usuario: categorias, subcategorias, marcas, fornecedores, unidades, qualidade; filtros e acoes de exportacao ficam organizados para caber no desktop e mobile.
- Preco promocional individual deve ser configurado na tela de Combos e Promocoes. O cadastro do produto deve manter o preco de venda base e pode apenas direcionar para a tela promocional.

## Replicacao
- Checkboxes de grupos de replicacao sao por filial em `PoliticaReplicacaoFilial`.
- `PoliticaReplicacao` por empresa e legado/fallback; nao usar para novas gravacoes de tela.
- A filial de origem precisa ter o grupo marcado para enviar, e a filial de destino tambem precisa ter o mesmo grupo marcado para receber.
- Fornecedores e fabricantes sao opcoes separadas.
- Ambos replicam por filial.
- Sincronizacao imediata deve ser tolerante e independente por grupo.
- Ficha tecnica deve ser pulada quando tabelas de producao ainda nao existirem no banco.
- Na central administrativa, ficha tecnica e qualidade ficam no grupo visual "Producao e qualidade".
- Qualidade usa `PoliticaReplicacaoFilial.replicar_qualidade` e replica:
  - `ParametroQualidadeCategoria` para padroes por categoria/subcategoria quando a categoria existir/vincular na filial destino.
  - `ParametroQualidadeProduto` quando a tabela existir e o produto ja existir na filial destino.
- A tela de qualidade permite cadastrar padroes por categoria/subcategoria e aplicar no produto sem duplicar parametros existentes.
- A tela de qualidade deve priorizar padroes por categoria/subcategoria quando aberta pela lista de produtos; qualidade por produto fica como uso especifico por busca/edicao.
- Qualidade tem busca de produto por ID, nome, codigo/referencia e codigo de barras.
- Produtos agora tem a tela "Combos e Promocoes":
  - a primeira visao lista todas as condicoes comerciais ativas da filial;
  - a visao inicial tem filtros por status/tipo: Todos, Ativas, Programadas, Finalizadas, Combos, Kits, Brindes, Categorias e Precos promocionais;
  - quando filtrar por `Combos`, os combos programados tambem precisam aparecer;
  - cada aba mantem o cadastro minimizado no topo e a listagem do tipo abaixo, com acoes de editar e inativar;
  - botoes de cadastro devem usar os nomes: "Criar combo", "Criar Kit de produtos", "Criar desconto por categoria";
  - combo por quantidade do mesmo produto, com faixas de desconto percentual/desconto em R$ e calculo visual de preco unitario atual, total atual sem combo, preco unitario com combo e total com combo;
  - se o produto do combo tiver preco promocional ou desconto por categoria ativo, o combo compara os candidatos e usa o menor preco por padrao, permitindo alternar para o preco original no cadastro/edicao;
  - combo e kit so puxam automaticamente preco promocional/desconto por categoria quando a promocao esta ativa, dentro da vigencia e cobre pelo menos 5 dias da semana; promocoes de 1 a 4 dias sao tratadas como esporadicas e ficam para escolha futura no PDV;
  - combo por quantidade suporta condicao da faixa `Quantidade` e `A partir de`; `A partir de` e maior ou igual ao valor informado;
  - no formulario do combo, a ordem e: nome do combo, produto, faixas, adicionar variacao, vigencia/status/botao de criar;
  - a vigencia de combo/promocao e opcional. Sem inicio, vale imediatamente; sem fim, nao tem prazo de termino;
  - combos, kits, descontos por categoria e precos promocionais em lote podem restringir os dias da semana em que valem; por padrao valem todos os dias;
  - formularios de criacao/edicao precisam permitir limpar datas e salvar datas vazias;
  - calendario customizado deve permitir navegar meses, selecionar, limpar e refletir mudancas feitas por JS;
  - status esperados: Ativo, Programado, Finalizada e Inativo. Programado usa cor azul; Finalizada usa alerta/amarelo com tooltip sobre editar e reativar com nova data;
  - kit de produtos diferentes, com produtos primeiro, soma total dos itens, desconto depois e total final; a baixa futura de estoque deve ser item por item;
  - cadastro/edicao de kit segue a ordem nome, itens, revisao financeira, vigencia/dias/status/acoes; descricao nao aparece no formulario nesta fase;
  - kit pode usar o melhor preco vivo dos itens quando a flag estiver marcada, respeitando preco promocional individual, desconto por categoria e o minimo de 5 dias da semana para aplicacao automatica;
  - brinde por produto permite definir um produto gerador de brinde, quantidade minima e um ou mais itens gratuitos;
  - brinde segue o mesmo desenho do kit, com resumo financeiro, vigencia, dias da semana, status, replicacao e edicao por clique/toque na listagem;
  - brinde pode usar o melhor preco vivo do produto gerador de brinde quando marcado, seguindo a mesma comparacao entre preco promocional individual e desconto por categoria e o minimo de 5 dias da semana;
  - no PDV futuro, o brinde deve aparecer como item gratuito vinculado a promocao e dar baixa no estoque do produto entregue como brinde;
  - desconto por categoria/subcategoria, inclusive opcao "todas as categorias", com desconto definido por linha de categoria/subcategoria;
  - cadastro em lote e listagem separada de produtos com preco promocional ativo.
- Combos, kits, brindes e descontos tem validade opcional, ativo e flag individual "replicar para filiais".
- A replicacao de combos, kits, brindes e descontos tambem copia os dias da semana selecionados.
- Replicacao de combos, kits, brindes, descontos por categoria e precos promocionais em lote e seletiva por filial destino.
- Depois de criada, a copia replicada vira independente. Desligar replicacao ou editar a origem nao altera copias antigas automaticamente.
- Quando a filial destino nao puder receber a promocao, a tela deve antecipar o bloqueio e informar o motivo. No backend, salvamentos parciais devem avisar quais filiais nao receberam a copia.
- Promocoes replicadas usam `id_externo` para rastrear origem e evitar sobrescrita por nome.
- Replicacao comum de preco de venda do produto nao deve carregar campos promocionais; preco promocional replica apenas pelo fluxo de promocoes.
- Regra visual permanente: precos e valores monetarios sempre com 2 casas decimais; casas extras apenas para quantidades/estoque/granel.
- Consumos internos/insumos de produto foram discutidos, mas ficaram fora de combos/promocoes; devem entrar futuramente em ficha tecnica/composicao/BOM, nao como promocao.

## Preferencias visuais do usuario
- O usuario exige alinhamento visual cuidadoso. Revisar altura de textos, status, botoes, cards e tabelas antes de entregar.
- Tema claro: branco/cinza claro com laranja como destaque.
- Tema escuro: preto/cinza escuro com azul como destaque; nao usar laranja como destaque principal no escuro.
- Botoes de acao principal devem ser solidos, bonitos e chamativos na medida certa.
- Evitar cabecalhos/cor de secao quando a listagem correspondente estiver vazia.
- Evitar campos e cards parecendo soltos/desalinhados.
- Precos e totais sempre com 2 casas decimais; nao mostrar `10,0000` ou `5,000` quando nao for necessario.

## Logs
- Produto tem log especifico e completo.
- Cadastros e central usam log generico:
  - clientes
  - fornecedores
  - transportadoras
  - representantes
  - empresas
  - filiais
  - usuarios
  - perfis
  - permissoes
- Logs precisam registrar antes/depois real.
- Campos tecnicos devem ser ignorados.
- Campos numericos reais devem ser normalizados para evitar ruido como `0.00` vs `0`.

## Proximo passo
Etapa de Combos e Promocoes encerrada em 18/05/2026. Foco atual: estoque, dentro do projeto inicial de unificacao do Thiago. Depois seguem producao, fiscal e financeiro mantendo as regras de replicacao, mobile, temas e auditoria.

## Handoff - Estoque iniciado em 18/05/2026

### Decisao principal
- Estoque nao replica saldo em nenhuma hipotese.
- O que pode existir futuramente, se for muito bem justificado, e replicacao seletiva de parametros cadastrais de controle, como minimo/maximo/ponto de reposicao. Mesmo assim, o padrao recomendado e filial independente, porque a realidade fisica muda por filial.
- Transferencia entre filiais nao e replicacao. E movimento operacional: saida na origem e entrada na filial destino, com rastreio.

### Base reaproveitada da versao do Thiago
- A versao do Thiago trouxe boas ideias de KPIs, lotes, alertas e inventario.
- O projeto atual ja tinha uma base mais forte em `apps/estoque`, com `Estoque`, `MovimentacaoEstoque`, `LoteProduto`, `Inventario`, `ItemInventario`, `AlertaVencimento` e `MovimentacaoService`.
- O reaproveitamento nesta etapa foi conceitual e incremental; nao copiar codigo antigo diretamente porque ele usa APIs/estrutura anteriores.

### Primeira organizacao aplicada
- Tela de saldo passou a ter KPIs de itens controlados, abaixo do minimo, zerados e valor em estoque.
- Busca de estoque passou a aceitar ID, codigo/referencia, codigo de barras e nome.
- Tela de saldo passou a listar todos os produtos ativos vinculados a filial, exibindo saldo zero quando ainda nao existe linha em `Estoque`.
- Cada linha de saldo ganhou atalho para movimentar o produto ja selecionado.
- Criada rota/tela de nova movimentacao manual.
- Historico de movimentacoes ganhou entrada direta para nova movimentacao e busca ampliada.
- Ajuste manual e transferencia foram mantidos como fluxos separados.
- Quantidades passaram a evitar zeros finais desnecessarios.
- Criacao de lote com quantidade inicial agora gera entrada de estoque pelo `MovimentacaoService`, mantendo saldo e historico sincronizados.
- Em lote ja criado, quantidade inicial fica bloqueada; correcao de quantidade deve ser feita por movimentacao/ajuste.
- Inventario basico criado:
  - abre snapshot dos produtos ativos da filial;
  - permite salvar contagem;
  - ao fechar, gera ajustes pelo `MovimentacaoService` com documento de inventario;
  - se marcado para bloquear movimentacoes, entradas/saidas comuns da filial ficam bloqueadas ate fechar/cancelar.

### Regras permanentes do estoque
- Toda alteracao de saldo deve passar por `MovimentacaoService`.
- Nunca editar `Estoque.quantidade_atual` diretamente fora do service.
- Servicos antigos de custo medio e FIFO/FEFO foram mantidos como fachada, mas agora delegam entrada/baixa para `MovimentacaoService`.
- Movimento precisa registrar quantidade anterior e posterior.
- Saida nao pode deixar estoque negativo, salvo regra futura explicita e controlada.
- Lote vencido nao deve ser vendido.
- Kit no PDV futuro baixa estoque item por item.
- Brinde no PDV futuro baixa estoque do produto entregue gratuitamente.
- Combo baixa o produto vendido normalmente.

## Handoff - Combos e Promocoes encerrado em 18/05/2026

### Contexto
- O usuario concluiu a etapa de promocoes antes de seguir para estoque.
- A tela principal e `Produtos > Combos e Promocoes`, rota `produtos:combo-promocao-list`.
- A entrada pela edicao do produto na area de precos promocionais tambem direciona para essa mesma tela, normalmente com `?aba=precos`.
- Preco promocional individual nao deve mais ser editado diretamente no cadastro do produto como superficie principal; o cadastro do produto conserva o preco de venda base e apenas direciona para promocoes.

### Arquitetura funcional atual
- Models principais em `apps/produtos/models/promocao.py`:
  - `PromocaoQuantidade`
  - `PromocaoQuantidadeFaixa`
  - `KitProduto`
  - `KitProdutoItem`
  - `BrindeProduto`
  - `BrindeProdutoItem`
  - `KitCategoria`
  - `KitCategoriaRegra`
- View principal em `apps/produtos/views/promocao.py`.
- Auditoria de promocoes em `apps/produtos/views/promocao_audit.py`.
- Calculo de preco vivo em `apps/produtos/services/preco_service.py`.
- Replicacao em `apps/produtos/services/replicacao_service.py` e helpers da view de promocao.
- Template principal em `apps/produtos/templates/produtos/promocao/list.html` e partials em `apps/produtos/templates/produtos/promocao/partials/`.

### Regras de negocio consolidadas
- Preco vivo comercial deve comparar preco de venda, preco promocional individual e desconto por categoria/subcategoria.
- Quando a flag de uso de melhor preco estiver marcada, usar o menor preco elegivel.
- Nao acumular desconto automaticamente; comparar candidatos e aplicar o menor.
- Desconto por categoria pode usar preco promocional individual como base apenas quando a opcao estiver marcada. A mensagem deve alertar que isso gera desconto em cima de desconto.
- Combo, kit e brinde so puxam automaticamente preco promocional individual ou desconto por categoria quando a promocao:
  - esta ativa;
  - esta dentro da vigencia;
  - tem pelo menos 5 dias da semana selecionados.
- Promocao com menos de 5 dias da semana e esporadica; nao entra automaticamente em combo/kit/brinde.
- Datas vazias significam:
  - sem inicio: inicio imediato;
  - sem fim: sem prazo de termino.
- Status padrao das listagens:
  - Ativo
  - Programado
  - Finalizada
  - Inativo
- Evitar nomes improvisados como `Usa promo`; usar termos mais intuitivos, como `Melhor preco`, quando representar uso de preco vivo externo.

### PDV futuro
- O PDV deve mostrar todas as promocoes elegiveis em modal.
- O PDV deve sugerir o menor preco, mas permitir escolha do vendedor.
- Brinde deve aparecer como item gratuito vinculado a promocao e baixar estoque do produto entregue.
- Kit deve baixar estoque item por item.
- Combo por quantidade deve respeitar:
  - `Quantidade`: quantidade exata;
  - `A partir de`: maior ou igual.

### Replicacao de promocoes
- Replicacao de promocoes e seletiva por filial destino.
- Filiais bloqueadas devem aparecer desabilitadas com motivo antes do salvamento.
- Backend tambem deve avisar quais filiais nao receberam copia.
- Depois de criada, copia replicada vira independente.
- Desligar replicacao na origem nao apaga nem atualiza copias antigas.
- Replicar novamente para filial que ja possui copia com mesmo `id_externo` nao deve sobrescrever; deve informar que ja existe uma copia independente.
- `id_externo` foi adicionado a combos, kits, brindes e descontos por categoria para rastrear origem sem depender de nome.
- Migrations relevantes:
  - `apps/produtos/migrations/0019_promocoes_id_externo.py`
  - `apps/produtos/migrations/0020_repara_id_externo_promocoes.py`
- Essas migrations foram ajustadas para serem idempotentes e tolerantes a estado parcial em producao.

### Bugs encontrados e corrigidos nesta etapa
- Erro 500 ao abrir tela autenticada de promocoes:
  - causa provavel: contexto auxiliar de log/replicacao ou schema parcial em producao;
  - correcao: migrations de reparo para `id_externo` e protecao para log/replicacao nao derrubarem a tela.
- Desconto por categoria nao entrava no melhor preco de combo/kit:
  - correcao: `PrecoService` compara preco individual e desconto por categoria.
- Status de desconto por categoria aparecia fora do dia indevidamente:
  - correcao: status/listagem foram alinhados com a logica das demais promocoes.
- Dias da semana no log apareciam como numeros:
  - correcao: exibicao convertida para nomes.
- Falta de log de criacao:
  - correcao: auditoria de promocoes passou a registrar criacao e edicao.
- Campos de preco aceitavam letras, setinhas e casas demais:
  - correcao: entradas monetarias passaram a ser tratadas como texto/decimal controlado e exibicao limitada a 2 casas.
- Tela de precos promocionais estava vertical demais:
  - correcao: layout reorganizado em linhas mais horizontais e resumo compacto.
- Listagens de brindes estavam pouco autoexplicativas:
  - correcao: listagem ganhou item vendido, brindes separados, resumo visual, validade/status/acoes.
- Tooltip nativo preto era ruim visualmente:
  - correcao: origem promocional deve usar balao visual proprio com texto claro e sem termos tecnicos como `Fonte` e `Base`.

### Riscos futuros
- PDV ainda nao consome essas regras; cuidado para nao duplicar logica em outro lugar. Usar `PrecoService`.
- Schema parcial no Railway pode causar 500 se novas colunas forem usadas direto em listagens. Preferir migrations idempotentes quando campo novo entrar em tela principal.
- Replicacao seletiva precisa ser testada em base real com varias filiais antes de considerar 100% fechada operacionalmente.
- Logs devem ser tolerantes: erro de auditoria nunca deve derrubar tela principal.
- Evitar voltar preco promocional para o cadastro do produto; isso quebraria a separacao entre preco base e regra promocional.
- CRM por cliente/grupo foi discutido como futuro, mas nao deve ser misturado na tela de promocoes de produto.

### Commits importantes recentes
- `29f7647 Protege tela de promocoes contra falhas auxiliares`
- `43666df Corrige migration de id externo das promocoes`
- `a9a715b Corrige default do id externo de promocoes`
- `b1cb0d4 Ajusta replicacao seletiva de promocoes`
- `bd81357 Corrige permissao e replicacao de promocoes`
- `aacbb77 Adiciona brindes em combos e promocoes`
- `553452b Limita promocoes automaticas por dias da semana`
- `27b95e2 Considera descontos ativos na criacao de combos`

## Deploy Railway
- O projeto usa Dockerfile; migrations e `ensure_quality_schema` rodam no `CMD` de inicializacao do container.
- Nao ha `railway.json` com Post-deploy no repositorio neste momento.
- Se o Railway ficar com deploy ativo em commit antigo mesmo apos push, enviar commit nao-vazio para disparar novamente o webhook/deploy.
- Quando o deploy nao iniciar, verificar primeiro plano/credito Railway e se o webhook do GitHub disparou.
- `manage.py migrate` local em SQLite pode falhar por migrations antigas com SQL especifico; para mudancas de schema recentes validar pelo menos `manage.py check`, `sqlmigrate app migration` e deploy em PostgreSQL/Railway.
- Depois da sequencia de ajustes de combos, ultimo estado conhecido funcional inclui os commits:
  - `2556c83 Restaura versao estavel de combos`
  - `9f472d4 Ajusta programados e calendario de combos`
  - `50b32de Corrige navegacao mensal do calendario`
- Depois do fechamento de promocoes, ultimo estado conhecido funcional em producao: `29f7647 Protege tela de promocoes contra falhas auxiliares`.
- Se a tela `/produtos/combos-promocoes/` voltar a dar 500, comparar contra esses commits e reaplicar correcao pequena, validando template/contexto antes do deploy.

## Fechamento do estoque MVP - 21/05/2026
- Documentacao consolidada em `docs/ESTOQUE_FECHAMENTO_MVP.md`.
- Checklist final de aceite do estoque consolidado no mesmo documento.
- Migrations pendentes de `core` e `fiscal` foram formalizadas para eliminar o aviso de producao.
- Mudancas paralelas de parametros/identidade visual da filial foram integradas ao pacote atual, preservando o fluxo combinado de sempre partir do ultimo `origin/main`.
- Validacao local completa executada explicitamente com 213 testes:
  - `apps.cadastros.tests`
  - `apps.compras.tests`
  - `apps.core.tests`
  - `apps.estoque.tests`
  - `apps.fiscal.tests`
  - `apps.pdv.tests`
  - `apps.produtos.tests`
- Regra permanente reforcada: estoque fisico, lote, reserva, inventario e movimentacao nunca replicam entre filiais.

## Contrato Estoque x PDV x Promocoes - 21/05/2026
- Contrato consolidado em `docs/CONTRATO_ESTOQUE_PDV_PROMOCOES.md`.
- Fonte unica para consulta de produto vendavel: `ProdutoVendavelService`.
- PDV e vendas passam a validar produto vendavel antes de vender/reservar.
- Resposta do contrato inclui saldo disponivel, custo atual, preco aplicado, margem, status comercial, lote obrigatorio e promocoes aplicaveis.
- Produto sem preco/custo valido ou promocao com margem negativa bloqueia venda no backend.
- Combo por quantidade entrou no preco vivo.
- Kit no PDV baixa componentes item a item.
- Brinde no PDV baixa o produto entregue gratuitamente com movimento de estoque `BRINDE`.

## Handoff - Estoque iniciado em 18/05/2026

### Estado atual
- Estoque e operacional por filial e nao deve ser replicado.
- Tela de saldo lista todos os produtos ativos vinculados a filial, inclusive sem linha em `Estoque`, com saldo zero.
- Movimentacoes manuais, ajustes, transferencias, lotes com entrada inicial e inventario passam por `MovimentacaoService`.
- Reservas e liberacoes de pedidos de venda tambem passam por `MovimentacaoService`, sem gerar baixa fisica antes do faturamento.
- Servicos legados de custo medio e FIFO/FEFO foram centralizados para nao escrever saldo fora do service.
- Alertas de estoque agora exibem vencimento de lote e estoque minimo na mesma tela.
- Alerta de estoque minimo considera produto-filial ativo mesmo sem linha consolidada de estoque, tratando disponibilidade como zero.
- Baixa por validade de lote vencido gera `MovimentacaoEstoque` com tipo `baixa_validade` e baixa todo o saldo atual do lote.
- Saida FEFO usa lotes quando o produto controla lote; quando nao controla, baixa diretamente o saldo consolidado pelo mesmo service.
- Reserva de produto com controle de lote valida lotes ativos e nao vencidos antes de confirmar disponibilidade.
- `python manage.py conferir_estoque` confere divergencias de saldo/disponibilidade/snapshot/lotes sem alterar dados; `--fix-disponivel` corrige apenas o campo derivado `quantidade_disponivel`.
- Produto que controla lote deve sempre movimentar com `lote_id`; devolucao de venda usa os lotes das saidas faturadas para retornar ao estoque.
- Ajuste de saldo pelo cadastro do produto deve ser ignorado para produto com lote; usar tela de lotes/movimentacoes.
- Inventario com `bloquear_movimentacoes=True` bloqueia movimentacao comum e nova reserva; ajuste do proprio inventario continua permitido pelo documento `INVENTARIO`.
- Suite inicial de estoque: `python manage.py test apps.estoque.tests.test_movimentacao_service --settings=config.settings.test`.
- Transferencia entre filiais exige produto ativo/vinculado na origem e no destino; isso evita criar saldo que nao aparece na tela da filial destino.
- Cada transferencia gera saida e entrada com mesmo `documento_numero` no formato `TRF-...`; cada movimento aponta para o outro via `documento_id`.
- Lote com status `ESGOTADO` volta para `ATIVO` quando uma entrada aumenta saldo positivo e o lote nao esta vencido.
- Detalhe do inventario mostra progresso, pendencias, divergencias, sobras e faltas valorizadas antes do fechamento.
- Detalhe do inventario exporta CSV da contagem para conferencia/auditoria.
- Lista/CSV de movimentacoes mostram origem/destino e movimento relacionado em transferencias; a busca tambem aceita ID de movimento/documento e filial destino.
- Suite atual de estoque: `python manage.py test apps.estoque.tests --settings=config.settings.test`.
- Permissoes do estoque ficaram mais granulares:
  - `ver`: abrir listas, detalhes, historico, inventarios, divergencias e reposicao.
  - `criar`: abrir novo lote e novo inventario.
  - `editar`: ajuste manual, movimentacao manual, transferencia e contagem/fechamento de inventario.
  - `cancelar`: cancelar inventario aberto/em contagem e baixar lote vencido por validade.
  - `exportar`: baixar CSV de saldo, movimentacoes, lotes, inventario e divergencias.
- Exportacao CSV de estoque agora e bloqueada no backend quando o perfil nao tem `pode_exportar`, mesmo que o usuario force `?export=csv`.
- Nova tela `Estoque > Reposicao` em `/estoque/reposicao/` lista produtos abaixo do ponto/minimo e transforma sugestao em pedido real.
- A reposicao gera pedidos de compra em rascunho via `CompraService`, agrupando por fornecedor. Produto sem fornecedor fica fora do pedido e recebe aviso.
- Para gerar pedido de reposicao, o usuario precisa de `estoque:editar` e `compras:criar`.
- Nova tela de divergencias de inventario fechado em `/estoque/inventarios/<id>/divergencias/`, com resumo de sobras/faltas/liquido, CSV e atalho para movimentacoes do inventario.
- Telas densas ganharam reforco mobile: tabelas comuns agora tem largura minima e rolagem horizontal estavel; reposicao e divergencias tem cards proprios no mobile.
- Suite de estoque agora cobre services, forms e views: `python manage.py test apps.estoque.tests --settings=config.settings.test` com 18 testes.
- Validacao Railway com dados reais foi concluida em 18/05/2026:
  - projeto `zucchini-renewal`, ambiente `production`, servico `erp-inoovated`;
  - deploy ativo no commit `3519c03`, status `SUCCESS`, 1 replica rodando;
  - dominio `https://inovated.up.railway.app`;
  - `python manage.py check` contra o Postgres publico passou sem erros;
  - logs recentes e HTTP logs nao mostraram 500;
  - login respondeu 200; `/estoque/` e `/estoque/reposicao/` redirecionaram para login com 302 sem 500;
  - renderizacao read-only das telas saldo, reposicao, movimentacoes, lotes e inventarios retornou 200 para as filiais 1 e 2.
- Snapshot de dados reais nessa validacao:
  - 3 filiais ativas;
  - Filial 1 `Polpa do Nordeste - Matriz Natal`: 3 produtos vinculados, 3 saldos, 4 movimentacoes;
  - Filial 2 `Polpa do Nordeste - Mossoro`: 3 produtos vinculados, sem saldos/movimentacoes ainda;
  - Filial 3 `Fiber - SP`: sem produtos vinculados no momento.
- Compras e vendas seguem modulos inacabados; por enquanto, tratar apenas os contratos que ja encostam no estoque:
  - entrada de compra efetivada deve criar lote/saldo/movimentacao via `MovimentacaoService`;
  - venda confirmada deve reservar estoque;
  - venda cancelada deve liberar reserva, inclusive quando ja estiver em separacao;
  - venda faturada deve liberar reserva e baixar saldo real;
  - preco promocional pode definir o preco comercial do item, mas nao pode alterar saldo, lote, reserva nem custo medio.
- Correcao aplicada apos QA Railway: opcoes auxiliares de replicacao de promocoes usavam `Filial.nome`, campo inexistente. Usar sempre `nome_fantasia` ou `razao_social`.
- Suite relacionada estoque/produtos/promocoes/contratos parciais de compras-vendas: `python manage.py test apps.estoque.tests apps.produtos.tests --settings=config.settings.test` com 52 testes.
- Validacao read-only contra Postgres de producao renderizou com status 200 para Filial 1 e Filial 2:
  - saldo;
  - reposicao;
  - movimentacoes;
  - lotes;
  - inventarios;
  - promocoes.
- QA real com rollback no Railway concluido em 19/05/2026:
  - migrations `compras.0002_compat_legacy_required_fields` e `vendas.0002_compat_legacy_required_fields` aplicadas para compatibilidade com colunas obrigatorias legadas do banco real;
  - compras e vendas continuam inacabados; a correcao cobre somente o contrato minimo ja encostado pelo estoque, sem concluir esses modulos;
  - filiais reais usadas: Filial 1 `Polpa do Nordeste - Matriz Natal` e Filial 2 `Polpa do Nordeste - Mossoro`;
  - o QA abriu transacao, criou dados prefixados com `QA ESTOQUE`, executou fluxos reais e forçou rollback no final;
  - fluxos validados: entrada de estoque, venda com preco promocional reservando e baixando estoque, transferencia entre filiais, compra com lote, baixa por validade, fechamento de inventario, geracao de pedido por reposicao, comando `conferir_estoque` e renderizacao de telas de estoque/promocoes;
  - dentro da transacao existiram 5 produtos, 1 fornecedor, 1 cliente, 5 movimentos, 1 pedido de compra e 1 inventario temporarios;
  - apos rollback: produtos=0, fornecedores=0, clientes=0, movimentos=0, pedidos_compra=0, inventarios=0 para o prefixo QA. Nenhum dado temporario ficou persistido.
- Entrada de Mercadoria / Recebimento Fiscal fase 1 iniciada em 19/05/2026:
  - entrada de NF evolui em `apps/compras` sem criar modulo paralelo;
  - origens suportadas na base: manual, XML, chave de acesso e manifesto fiscal;
  - importacao XML guarda XML original, emitente, destinatario, chave, totais e itens;
  - regra do usuario: destinatario da nota pode ser qualquer CPF/CNPJ. Divergencia com CNPJ da filial gera apenas alerta operacional (`destinatario_documento_diferente`), nunca bloqueio;
  - fornecedor desconhecido vindo de XML com documento e razao social e cadastrado automaticamente na filial; `Fornecedor nao cadastrado`/`fornecedor_pendente=True` fica como fallback para XML/chave sem dados suficientes;
  - item da nota pode ficar sem produto interno, mas a finalizacao bloqueia enquanto houver produto sem vinculo;
  - equivalencia por EAN/codigo fornecedor foi preparada em `ProdutoCodigoBarras` e `ProdutoFornecedorEquivalencia`;
  - conferencia de itens sugere produtos internos por similaridade de nome, NCM e unidade quando EAN/equivalencia nao encontram vinculo; sugestao nunca vincula automaticamente;
  - confirmacao em massa da conferencia permite escolher entre sugestoes recalculadas no servidor, editar fator/unidade/lote/validade e ignora qualquer produto que nao faca parte das sugestoes seguras daquele item;
  - item sem vinculo pode cadastrar produto pelo XML, preenchendo nome, EAN, NCM, fornecedor, unidade, custo e equivalencia, mas estoque continua parado ate finalizar a entrada;
  - cadastro rapido pelo XML herda controle de lote/validade quando o item veio com rastro e reaproveita produto ja criado por EAN/equivalencia em NF com multiplos lotes, evitando duplicidade;
  - tela de fornecedor pendente continua disponivel para casos incompletos: permite criar fornecedor real a partir dos dados do XML, vincular fornecedor existente ou continuar pendente; ao resolver, equivalencias pendentes daquele CNPJ XML sao atualizadas;
  - fator de conversao transforma quantidade da nota em quantidade de estoque, e o custo unitario e convertido para a unidade de estoque antes de movimentar;
  - parcelas/faturas do XML entram como pre-lancamento financeiro pendente; conta a pagar so e criada por acao manual depois da entrada efetivada;
  - telas de diferencas e finalizacao recalculam a leitura de lote/validade/quantidade antes de renderizar, para nao mostrar uma entrada como pronta quando flags antigas estiverem desatualizadas;
  - finalizacao continua usando `MovimentacaoService`, preservando a regra de nunca escrever saldo direto;
  - Manifesto Fiscal/DF-e tem base de config, documentos e logs, com tela inicial e consulta SEFAZ por distribuicao DF-e em homologacao quando as travas estiverem habilitadas;
  - documento do Manifesto com `xml_completo` pode virar Entrada de NF pelo botao `Importar entrada`; o fluxo valida se a chave do XML pertence ao manifesto, reaproveita o importador XML, cadastra fornecedor automaticamente quando houver dados do emitente e vincula uma entrada ja existente pela mesma chave sem duplicar;
  - lista do Manifesto ganhou acoes de `Abrir entrada`/`Importar entrada` e versao mobile em cards para evitar overflow da chave de acesso;
  - Manifesto sem XML completo ganhou acao `Anexar XML`, com tela para upload ou XML colado. O XML e salvo somente se a chave interna da NF-e bater com a chave do manifesto; tambem ha acao `Salvar e importar entrada`;
  - consulta DF-e passa por `DFeClient` seguro. O modo padrao `local` nao acessa SEFAZ, nao usa certificado e nao cria documentos falsos; o modo `sefaz` consulta `NFeDistribuicaoDFe` apenas com `FISCAL_DFE_ENABLE_REAL_CONSULTA`, certificado A1 valido e senha em ambiente;
  - manifestacoes fiscais reais continuam bloqueadas. `Dar ciencia`, `desconhecer` e `nao realizada` registram apenas estado local/log no ERP nesta etapa;
  - tela de configuracao DF-e mostra prontidao da integracao: modo, flag de consulta real, certificado A1, senha via `FISCAL_DFE_CERT_PASSWORD` e eventos reais, sem gravar senha no banco nem logar segredo;
  - `SefazDFeClient` bloqueia consulta real em camadas: flag, certificado, senha, validacao offline do A1, bloqueio de producao por padrao e cooldown de `ultimo_nsu == max_nsu` para evitar consumo indevido;
  - testes adicionais cobrem importacao de manifesto para entrada, bloqueio de manifesto sem XML, vinculo com entrada existente e recusa de XML de outra chave;
  - testes do anexo de XML cobrem renderizacao da tela, salvamento local, recusa de chave divergente, botao na lista e salvar+importar para conferencia;
  - testes de seguranca DF-e cobrem consulta local vazia, bloqueio de SEFAZ real por padrao, bloqueio de eventos reais, sync por client fake e preservacao de documento ja importado;
  - QA com `xmls teste.zip`: 12 arquivos lidos, 10 NF-e importaram localmente com rollback, 1 XML era invalido e 1 tinha chave duplicada; apos regra de fornecedor automatico, as 10 NF-e validas criaram 10 fornecedores unicos na transacao e ficaram com `fornecedor_pendente=False`; no Railway/Postgres foram testadas 4 XMLs representativas em 2 filiais reais, com 2 imports por filial, 2 invalidacoes esperadas por filial e `rollback_ok=True`;
  - suite local relacionada: `python manage.py test apps.compras.tests.test_entrada_recebimento apps.estoque.tests apps.produtos.tests --settings=config.settings.test`.

### Riscos permanentes do estoque
- Nunca atualizar `Estoque.quantidade_atual`, `quantidade_disponivel`, lote ou custo medio diretamente fora de service transacional.
- Nunca atualizar `Estoque.quantidade_reservada` diretamente fora de service transacional.
- Todo `lote_id` recebido por movimentacao precisa pertencer ao mesmo produto e filial da operacao.
- Lote vencido/bloqueado nao pode sair por venda normal; a excecao permitida e somente a baixa por validade auditada.
- Nunca permitir saida/entrada manual de produto controlado por lote sem lote identificado.
- Nunca replicar saldo, reserva, lote, inventario ou movimentacao entre filiais.
- Transferencia entre filiais e movimento bilateral auditado, nao replicacao.
- Transferencia nao pode criar estoque para produto sem vinculo ativo na filial destino.
- Produto sem movimento ainda precisa aparecer em saldo e alertas quando estiver vinculado a filial.
- Nao remover campos legados obrigatorios de compras/vendas sem migration planejada; o banco Railway ainda os exige e o estoque depende desses contratos para testar entrada, reserva e baixa.

## Handoff - PDV, promocoes e estoque preparado em 20/05/2026

### Contrato inicial
- O PDV ainda nao esta fechado funcionalmente, mas a venda finalizada ja deve respeitar o caminho correto:
  - buscar produto somente na filial ativa;
  - recalcular preco no backend por `PrecoService`;
  - gravar snapshot de origem do preco no item do PDV;
  - gravar snapshot de custo unitario no item do PDV;
  - baixar estoque pelo `MovimentacaoService`;
  - rastrear os IDs de `MovimentacaoEstoque` gerados no item do PDV.
- O front pode mandar `valor_unitario`, mas venda finalizada nao deve confiar nesse valor como fonte de verdade. O backend recalcula o preco vivo.
- Busca de produtos do PDV e estado inicial agora retornam `preco`, `preco_base`, origem do preco e saldo disponivel para preparar a interface futura.

### Regras para evoluir vendas
- Nao duplicar calculo de promocao dentro do PDV. Usar `PrecoService`.
- Nao baixar saldo direto no PDV. Usar `MovimentacaoService`.
- Promocao de produto e desconto por categoria podem resolver preco automaticamente quando vigentes.
- Combo, kit e brinde ainda precisam de fluxo proprio no PDV:
  - combo baixa o produto vendido normalmente;
  - kit baixa item por item;
  - brinde registra item gratis e baixa o produto entregue;
  - quando houver multiplas opcoes promocionais, o PDV deve mostrar modal e sugerir o menor preco, sem aplicar tudo automaticamente.
- Produto tipo servico nao baixa estoque.
- Venda finalizada sem estoque suficiente deve falhar e fazer rollback da venda inteira.

## Handoff - financeiro e posicao diaria em 24/08/2026

- O resumo funcional, tecnico, visual e de producao desta rodada esta em `docs/FINANCEIRO_POSICAO_DIARIA_RESUMO_2026-08-24.md`.
- Ultimo estado funcional implantado: commit `570dd5b1`, deploy Railway `6ced0330-f685-4fc1-8e90-d81699fab056`, status `SUCCESS`.
- Migration `financeiro.0049_reforcar_tarifas_saida_orenda` aplicada no PostgreSQL de producao.
- Suite de fechamento: 40 testes de posicao diaria e formas de pagamento passaram.
- Previsoes de recebimentos e pagamentos iniciam em `Hoje`, ficam antes dos saldos bancarios e sao recolhiveis.
- Pagamentos previstos abrem diretamente a quitacao da conta.
- Saidas PIX ORENDA e boleto possuem tarifa de R$ 0,50 reforcada por migration idempotente.
- Antes de continuar o financeiro, ler o resumo acima e `docs/UI_RULES.md`, especialmente as regras que proíbem cabecalho local colorido/degradê e listagens altas.
- Nao retomar cadastro de funcionarios nesta frente; o usuario encerrou explicitamente esse assunto.
