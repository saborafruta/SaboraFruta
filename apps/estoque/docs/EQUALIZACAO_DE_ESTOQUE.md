# Equalização de Estoque

Módulo de redistribuição inteligente de estoque entre filiais: identifica excesso
numa filial e falta em outra, calcula quanto e quão urgente é transferir, e leva
a sugestão até a transferência real com rastreabilidade e aprovação por valor.

## Arquitetura

Tudo vive em `apps/estoque`, organizado por responsabilidade, não por tela:

```
models/          equilibrio_estoque.py não tem model -- é 100% leitura, calculado
  faixa_cobertura.py            FaixaCoberturaEstoque       (Ruptura..Excesso por produto/categoria/empresa)
  configuracao_abc.py           ConfiguracaoAbcEstoque      (ajuste de meta por classe A/B/C)
  configuracao_demanda.py       ConfiguracaoDemandaPonderada
  solicitacao_transferencia.py  SolicitacaoTransferencia, NivelAprovacaoTransferencia
  snapshot_equalizacao.py       SugestaoEqualizacaoSnapshot (histórico -- só a automação escreve aqui)

services/        onde a inteligência mora; toda regra de negócio passa por aqui
  equilibrio_estoque.py     calcular_equilibrio()   -- o motor: quanto/de onde/pra onde/por quê
  posicoes_estoque.py       calcular_posicoes()     -- visão por produto+filial (dashboard/mapa/central)
  cobertura_service.py      classifica dias de cobertura em Ruptura..Excesso
  configuracao_abc_service  ajusta meta por classe ABC
  demanda_inteligente.py    média ponderada, dia da semana, sazonalidade (leitura à parte)
  simulador_transferencia.py  prévia antes/depois sem mexer em nada
  aprovacao_transferencia.py  alçada por valor, segregação de funções, FEFO na aprovação
  alertas_equalizacao.py    6 alertas como notificação in-app (condição, não evento)
  indicadores_performance.py, relatorios_equalizacao.py
  snapshot_equalizacao.py   grava o histórico (chamado pela automação, não pelas telas)

views/ + templates/   telas HTML (dashboard, mapa, central, recomendações, simulador, aprovação, relatórios)
api/                   API REST (ver "API" abaixo)
tasks/equalizacao.py   pipeline diário Celery (existe, não está ativado -- ver o arquivo)
tests/                 um arquivo de teste por área (ver "Testes")
```

**Regra de ouro**: `calcular_equilibrio()` e `calcular_posicoes()` nunca escrevem no
banco. Toda mudança de estoque passa por `MovimentacaoService`, que sempre gera uma
`MovimentacaoEstoque` auditável -- a equalização nunca altera saldo diretamente.

## O algoritmo (`equilibrio_estoque.calcular_equilibrio`)

Para cada produto vinculado a 2+ filiais:

1. **Demanda diária** = vendido no período (PDV + pedido de venda faturado) / dias.
2. **Reserva** de cada filial = `max(mínimo × ajuste ABC, segurança, demanda × dias_meta)`,
   onde `dias_meta = max(dias_cobertura escolhido, lead time do fornecedor) + ajuste ABC`.
3. **Meta** = reserva própria + fatia do excedente de rede (proporcional à demanda),
   nunca acima do `estoque_maximo` do produto.
4. **Déficit** = meta − saldo − o que já está a caminho por compra em aberto.
5. **Excedente ofertável** = saldo − meta, mas nunca mais do que está em lote ATIVO
   e não vencido, para produtos com controle de lote.
6. **Casamento**: destinos ordenados por urgência (menor cobertura em dias primeiro,
   sem giro por último); origens por excedente. Nunca oferta mais que o excedente real.
7. **Score 0-100** (ver abaixo) reordena a lista final -- a alocação de quantidade já
   aconteceu no passo 6, o score só decide a ordem de exibição/prioridade.

### Score de prioridade

Pontos somam, capados em 100: ruptura no destino (+30), abaixo do mínimo (+25), venda
acima da média da rede (+20), cobertura < 3 dias (+15), pedido de venda pendente (+10),
curva A (+10), produto parado na origem (+10), lote perto de vencer (+5). Compra em
aberto que já cobre parte do déficit **reduz** até 15 pontos, proporcional à fatia coberta.

## Fluxo de transferência e aprovação

```
Sugestão (calculada, não persistida)
   → "Criar transferência" entra pelo GATE (views/aprovacao_transferencia.TransferenciaGateView)
        dentro da alçada do perfil  → executa na hora (como sempre foi)
        acima da alçada             → SolicitacaoTransferencia PENDENTE
   → alguém com alçada suficiente aprova (executa de verdade, resolve lote por FEFO)
     ou rejeita (nunca mexe em estoque)
   → MovimentacaoService.transferir_entre_filiais() credita o destino na hora
   → ConferenciaTransferencia audita o recebimento (Aguardando → Conferida/Com divergência)
   → etapa (Aprovada→Separando→Expedida→Em trânsito→Recebida) é rastreamento visual,
     não um gate sobre o estoque -- ver o docstring de ConferenciaTransferencia.Etapa
```

Segregação de funções: quem solicita não pode aprovar nem rejeitar a própria solicitação
(`aprovacao_transferencia.aprovar_solicitacao`/`rejeitar_solicitacao`).

## Auditoria

Toda ação sensível (solicitar, aprovar, rejeitar, transferir) chama
`apps.core.services.auditoria.registrar_auditoria`, que grava em `RegistroAuditoria`:
usuário, filial, ação, objeto, dados antes/depois (JSON), justificativa, IP e user-agent
quando a chamada carrega o `request`. Consulte pelo Django admin ou
`RegistroAuditoria.objects.filter(objeto_tipo="estoque.solicitacaotransferencia", ...)`.

## API REST

`GET/POST /api/estoque/equalizacao/...` (10 endpoints, ver `apps/estoque/api/urls.py`).
Autenticação por **sessão** (a mesma do resto do ERP), não JWT: o projeto tem
`rest_framework_simplejwt` configurado, mas nenhum endpoint de token existe em lugar
nenhum do sistema, e não dava pra' construir um genérico aqui -- nesta arquitetura
multi-tenant (um banco Postgres por empresa), autenticar por usuário/senha exige saber
ANTES em qual banco checar a credencial, o mesmo problema que o login por sessão já
resolve. RBAC via `TemPermissaoEstoque` (mesmo `tem_permissao('estoque', ação)` das
telas HTML).

## Automação (Celery)

`apps/estoque/tasks/equalizacao.py` tem o pipeline diário completo (recalcular demanda
→ ... → snapshot + alertas), mas **não está no `beat_schedule`** de `config/celery.py`
-- decisão explícita: as tarefas existem, prontas para ligar, sem rodar sozinhas em
produção até alguém decidir habilitar (só adicionar a entrada, comentário no próprio
arquivo explica como).

## Rodando uma demonstração

```bash
python manage.py seed_equalizacao_demo
```

Cria (ou usa) duas filiais de uma empresa existente com um produto sobrando numa e
quase em ruptura na outra -- abra "Equilíbrio de estoque" e a sugestão já aparece.

## Testes

`apps/estoque/tests/test_equilibrio_estoque.py` é o núcleo (motor, score, ABC, lote/FEFO,
regras obrigatórias -- reservado, abaixo do mínimo, perecível, permissões). Os demais
arquivos cobrem cada peça isoladamente: `test_aprovacao_transferencia.py` (alçada,
segregação, auditoria, concorrência -- este último só roda de verdade em Postgres),
`test_api_equalizacao.py`, `test_alertas_equalizacao.py`, `test_snapshot_equalizacao.py`,
`test_relatorios_indicadores.py`, `test_demanda_inteligente.py`, `test_seed_equalizacao_demo.py`,
entre outros. Rodar tudo:

```bash
python manage.py test apps.estoque --settings=config.settings.test
```
