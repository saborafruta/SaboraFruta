# UI_RULES.md

## Temas
- Existem dois temas principais:
  - Tema claro: branco/cinza claro com laranja como destaque e cor de acao principal.
  - Tema escuro: preto/cinza escuro com azul como destaque e cor de acao principal.
- Nao inverter as cores: no tema escuro, destaque principal deve ser azul, nao laranja.
- Tokens praticos:
  - Tema claro: primario laranja `#f15a24` / `#e8824a`; hover em tons de laranja mais fechado.
  - Tema escuro: primario azul `#2563eb` / `#1d4ed8`; hover em tons de azul mais claro.
  - Acoes destrutivas continuam vermelhas nos dois temas.
  - Avisos/alertas podem usar amarelo/vermelho conforme semantica, mas nao como cor principal da tela.
- Ao criar CSS especifico por tela, sempre declarar os dois estados:
  - `body.tema-claro ...` usando laranja para acao/destaque.
  - `body:not(.tema-claro) ...` usando azul para acao/destaque.
- Botoes principais devem ser solidos, elegantes e coerentes com o tema. Evitar botao principal com borda fraca quando a tela pede chamada de acao.
- Botoes secundarios podem ser outline, mas precisam parecer intencionais e alinhados.

## Mobile-first
Toda tela deve:
- funcionar em mobile
- evitar overflow
- ter modais responsivos
- funcionar em tablets

## Numeros e dinheiro
- Precos e valores monetarios sempre aparecem com 2 casas decimais.
- Nao exibir 4 casas decimais em preco, promocao, combo, kit ou desconto.
- Mais de 2 casas decimais ficam reservadas apenas para quantidades de estoque, itens a granel e medidas tecnicas.
- Quantidades podem ser exibidas sem zeros desnecessarios quando forem inteiras, por exemplo `5 un.` em vez de `5,000`.
- Desconto percentual deve exibir `%` depois do numero.
- Buscas de produto devem aceitar ID, codigo/referencia, codigo de barras e nome quando a tela tiver autocomplete de produto.
- Resultado de busca de produto nao deve mostrar referencia/codigo duplicado. Se o label ja contem `[CODIGO]`, a linha secundaria deve priorizar ID, barras, categoria e preco.
- Preco promocional ativo deve ter destaque visual sem aumentar demais a largura da linha; mostrar preco original riscado e tooltip/contexto quando houver vigencia.

## Alinhamento e acabamento
- O usuario valoriza muito alinhamento visual. Antes de concluir, verificar se textos, botoes, status, cards e tabelas estao na mesma altura e com espacamentos consistentes.
- Evitar elementos espalhados ou parecendo soltos. Texto explicativo e botao de acao devem compartilhar a mesma grade/linha visual quando forem relacionados.
- Nunca deixar cabecalho/cor de secao quando a respectiva listagem estiver vazia. Se nao houver linhas, esconder a secao inteira ou mostrar um estado vazio simples.
- Listagens de desktop nao devem depender de barra lateral/horizontal. Otimizar larguras, reduzir colunas secundarias e compactar campos curtos para manter `Acoes` sempre visivel dentro da area util.
- Em tabelas com cards internos, alinhar texto da linha ao centro visual do card, nao acima dele.
- Acoes de tabela devem usar icones ja usados no projeto para editar, ativar/inativar e confirmar, mantendo tooltip/acessibilidade quando necessario.
- Evitar cards dentro de cards sem necessidade. Preferir blocos limpos, bordas leves e respiracao consistente.
- Textos explicativos devem ser diretos e uteis, mas bem alinhados. Se forem alertas ou regras importantes, usar vermelho com cuidado e alinhado ao inicio do formulario.
- O `Voltar` principal da pagina fica no layout base (`templates/_base.html`) e deve seguir o tema: laranja no claro, azul no escuro. Templates de pagina nao devem criar outro `Voltar` no topo.
- O `Voltar` do layout deve levar para a ultima tela distinta visitada no sistema, ignorando repeticoes da mesma URL no historico do navegador.

## Calendarios
- Todo calendario customizado precisa permitir:
  - abrir e fechar sem quebrar layout;
  - avancar e voltar meses;
  - selecionar data;
  - limpar data;
  - salvar campo vazio quando a data for opcional;
  - refletir visualmente mudancas feitas por JS em edicao.
- Datas exibidas ao usuario devem ficar no formato brasileiro `dd/mm/aaaa`.
- Datas vazias em promocoes significam:
  - sem inicio informado: inicio imediato;
  - sem fim informado: sem prazo de termino.
- Dias da semana em combos/promocoes devem ficar proximos da vigencia, em controle recolhivel/compacto, com todos os dias pre-selecionados por padrao, acoes de marcar/desmarcar todos e visual coerente nos temas claro/escuro.

## Cadastros com listagem
- Nunca criar uma faixa, gradiente ou cabecalho colorido dentro do conteudo para repetir o titulo da pagina. O titulo oficial vem apenas de `page_title` no layout base.
- Templates de pagina nao devem usar `<header>` para barras locais de titulo/acoes, pois o CSS global de cabecalho pode colori-las. Acoes locais devem ficar em um `<nav>` ou `<div>` neutro, compacto e sem repetir titulo/subtitulo.
- Listagens operacionais devem usar tabela ou linhas compactas. Nao transformar cada registro em um card alto de largura total quando os dados cabem em colunas.
- Quando a tela tiver cadastro e listagem no mesmo lugar, o cadastro deve ficar minimizado/acionavel no topo e a listagem abaixo.
- O botao minimizado deve ser compacto, claro e com `+`; nao usar uma caixa horizontal gigante.
- Deve haver respiro entre o botao/formulario e a listagem.
- Quando o formulario abrir, evitar espacos vazios grandes dentro das linhas. Use grids equilibrados, cards de resumo e quebras responsivas.
- Em mobile, o mesmo formulario deve virar uma coluna unica com botoes em largura total quando fizer sentido.
- Formularios de criacao/edicao devem ter acao principal primeiro e cancelar ao lado direito quando o usuario puder desistir.

## Combos e promocoes
- Nomes dos botoes de cadastro:
  - Criar combo
  - Criar Kit de produtos
  - Criar desconto por categoria
  - Criar brinde
- Quando estiver editando uma promocao existente, o botao principal deve indicar edicao, por exemplo `Salvar alteracoes`, e nao `Criar`.
- Texto explicativo deve ser pratico, explicando quando usar a funcionalidade, nao apenas descrevendo campos.
- Combo por quantidade deve mostrar resultado em cards: preco unitario atual, total atual sem combo, preco unitario com combo e total com combo.
- Kit deve mostrar produtos antes do desconto; depois exibir total antes do desconto, desconto aplicado e total depois do desconto.
- Cadastro/edicao de kit deve seguir a ordem: nome do kit, produtos, revisao do kit, vigencia/dias da semana/status/acoes. Nao usar descricao no formulario do kit nesta fase.
- Kit deve permitir usar precos promocionais ativos dos produtos quando existirem, com flag visivel apenas nesse contexto e calculo/revisao respeitando a escolha.
- Brinde deve seguir o padrao visual do kit: nome, produto gerador de brinde, itens gratuitos, resumo financeiro, vigencia/dias da semana/status/acoes, listagem abaixo e acao de editar por icone ou toque no mobile.
- Brinde pode usar o melhor preco vivo do produto gerador de brinde quando houver promocao individual ou desconto por categoria elegivel; o resumo deve mostrar origem do preco e valor dos itens gratuitos que futuramente baixarao estoque no PDV.
- Em brinde, o item entregue gratuitamente deve aparecer como `Gratis`, nao como desconto monetario.
- Listagem de brinde deve ser compacta e autoexplicativa:
  - coluna `Brinde` com nome legivel;
  - coluna `Item vendido`;
  - coluna `Brindes`;
  - itens gratuitos separados visualmente entre si;
  - validade, status e acoes sempre visiveis no desktop;
  - no mobile, toque/clique na linha deve permitir editar.
- Desconto por categoria deve permitir aplicar valor para todas as linhas e limpar todas, sem poluir o layout.
- Na tela de combo, o botao `Criar combo` deve ficar alinhado ao lado direito da area superior/listagem, com destaque solido do tema.
- A tela de combo deve explicar de forma pratica: `Ex.: 1 unidade custa R$ 10,00. A partir de 3 unidades, o valor unitario fica R$ 8,00. Na compra de 3 unidades, cada unidade sai por R$ 8,00.`
- O campo de produto nao deve ocupar largura exagerada depois de selecionado; ao lado deve aparecer o preco unitario atual.
- A barra/faixa de regras do combo deve manter: valor total sem combo, valor unitario no combo e valor total com combo.
- Campo de desconto deve se chamar `Desconto (% ou R$)`.
- Vigencia precisa mostrar aviso vermelho, alinhado ao lado esquerdo do formulario: `Nao e obrigatorio. Se nao informar datas, o combo comeca a valer de imediato e fica sem prazo de termino.`
- Controle de dias da semana deve valer para combo por quantidade, kit de produtos, brinde por produto, desconto por categoria/subcategoria e preco promocional em lote.
- Combo por quantidade deve usar o melhor preco vivo do produto por padrao quando existir, comparando preco promocional individual e desconto por categoria, mostrar o preco original ao lado e permitir desligar `Usar preco promocional`; essa opcao so deve aparecer quando existir preco vivo automatico.
- Preco vivo automatico em combo/kit exige promocao ativa, dentro da vigencia e com pelo menos 5 dias da semana selecionados. Promocoes com 1 a 4 dias sao esporadicas e nao entram automaticamente no combo/kit.
- A mesma regra de preco vivo automatico vale para brinde: minimo de 5 dias da semana para puxar automaticamente.
- Se existir preco vivo automatico, usar o termo visual `Melhor preco` em listagens, evitando termos curtos demais como `Usa promo`.
- Alertas de produto recebendo desconto de outra promocao devem ser claros e curtos:
  - `Atencao: esse produto ja esta recebendo desconto de outra promocao.`
  - `Se deixar essa opcao marcada, o preco com desconto sera utilizado.`
- Mensagem de desconto por categoria deve alertar desconto sobre desconto quando a opcao estiver marcada.
- Origem de preco promocional deve ficar perto do preco, com icone informativo convidativo.
- Tooltip de origem nao deve usar tooltip preto nativo. Usar balao visual proprio, legivel nos temas claro e escuro.
- Tooltip de origem deve explicar em linguagem humana:
  - se veio de promocao individual ou desconto por categoria;
  - nome da promocao;
  - prazo ou ausencia de prazo;
  - preco aplicado.
- Nao mostrar termos tecnicos como `Fonte` e `Base` no tooltip do usuario.
- Listagem de combos deve separar variacoes/faixas em linhas, uma por faixa, quando isso melhorar leitura.
- Colunas esperadas da listagem de combo: Produto, Nome, Faixas, Validade, Status e Acoes.
- Faixa de combo deve exibir: Quantidade ou A partir de, Desconto, Unitario normal, Unitario combo e Valor total.
- `A partir de` significa maior ou igual a quantidade informada.
- Status da promocao/combo:
  - Ativo em verde.
  - Programado em azul.
  - Finalizada em amarelo/alerta, com tooltip informando que ao editar e colocar nova data pode ativar novamente.
  - Inativo discreto/cinza.
- Filtros da visao inicial devem permitir ver Todos, Ativas, Programadas, Finalizadas e cada tipo separado: Combos, Kits, Brindes, Categorias e Precos promocionais.
- No filtro por tipo, incluir tambem registros programados e finalizados quando o filtro de status permitir. Exemplo: filtro `Combos` precisa trazer combo programado.
- Se uma categoria/listagem da visao inicial nao tiver registros, nao mostrar cabecalho nem barra colorida vazia daquela categoria.
- Produto com preco promocional em lote e combos/kits/descontos por categoria podem ter campos diferentes; por isso a visao inicial deve agrupar por tipo quando as colunas nao forem equivalentes.

## Sidebar
- nao quebrar
- nao travar
- nao abrir ao trocar tema

## Logs e modais
- Modal de log precisa funcionar em mobile e desktop.
- Filtros de log nao podem estourar largura.
- Lista de alteracoes deve quebrar linha em textos longos.
- Valores antes/depois devem ser legiveis no tema claro e escuro.
- Evitar mensagens tecnicas ao usuario quando a operacao principal salvou corretamente.
- Log de promocoes deve usar titulo curto `Log`.
- Log de dias da semana deve mostrar nomes dos dias, nao numeros.
