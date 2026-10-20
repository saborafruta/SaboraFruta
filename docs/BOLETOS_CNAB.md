# Boletos: remessa e retorno CNAB

## Fluxo implementado

O iTED segue o fluxo operacional do TOTVS Gestão Financeira:

1. configurar banco, agência, conta, carteira, convênio/código de transmissão e layout;
2. selecionar títulos em aberto cuja forma de pagamento é boleto;
3. gerar uma remessa, vincular cada título a um `Boleto` e baixar o arquivo;
4. enviar o arquivo pelo canal disponibilizado pelo banco;
5. importar o retorno;
6. validar banco, tamanho, estrutura e duplicidade do arquivo;
7. registrar cada ocorrência e aplicar a baixa no Contas a Receber quando houver liquidação.

A operação fica em **Financeiro > Remessa e Retorno**. A configuração da conta
fica em **Financeiro > Contas Bancárias**.

## Segurança e isolamento

- remessas e retornos são sempre filtrados pela filial ativa;
- os registros são gravados no banco do tenant selecionado;
- o SHA-256 impede o reprocessamento do mesmo retorno na mesma filial;
- a baixa usa o serviço transacional já utilizado pelo Contas a Receber;
- ocorrências desconhecidas ou sem boleto correspondente ficam registradas,
  mas não alteram títulos;
- nenhum dado bancário ou arquivo cru é gravado no Banco Gerencial.

## Layout e homologação

A primeira entrega gera e lê **CNAB 240 de cobrança**, com header de arquivo,
header de lote, segmentos P/Q, trailers e leitura dos segmentos T/U. CNAB 400 é
identificado, mas seu processamento é bloqueado até existir um adaptador do
banco.

CNAB não é um contrato universal: bancos mudam campos, carteiras, dígitos e
códigos mesmo quando usam o padrão FEBRABAN. Por isso a conta nasce com
`remessa_homologada=False`. Antes de uso real:

1. obter do banco o manual vigente da carteira contratada;
2. gerar uma remessa pequena de homologação;
3. validar posições, nome do arquivo, Nosso Número e dígitos;
4. importar o retorno de homologação;
5. conferir confirmação, rejeição, liquidação, tarifa e baixa parcial;
6. somente então marcar o layout como homologado.

Se o banco exigir uma variação, o ponto de extensão é
`apps/financeiro/services/cnab_cobranca.py`; o adaptador deve ganhar fixtures
reais anonimizadas e testes antes de ser liberado.

## Referências funcionais

- TOTVS Protheus, processamento de retorno: `FINA200`;
- TOTVS Protheus, arquivo de cobranças: `FINA150`;
- TOTVS RM, remessa de cobrança e retorno CNAB 240;
- FEBRABAN, layout CNAB 240 vigente e manual específico do banco.
