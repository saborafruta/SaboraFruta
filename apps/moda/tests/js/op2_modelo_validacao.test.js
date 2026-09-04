const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  validarModeloOp2, op2AlternarMultisselecao, op2ResumoMultisselecao,
  op2EstruturaPadraoNaoMultipla, op2NormalizarUidsItens,
  op2NovaConfiguracaoConjunto, op2CopiarCamisaParaCalcao,
  op2TotalComponenteConjunto, op2PreservarEstruturaAoTrocarTipo, validarConjuntoOp2,
} = require('../../../../static/js/op2_modelo_validacao.js');

const grupos = {
  camisa: { label: 'Camisa', campos: { tipo_impressao: ['SILK', 'N/A'], malha: ['PP', 'N/A'], gola: ['POLO', 'N/A'] } },
  colete: { label: 'Colete', campos: { tipo_impressao: ['N/A'], malha: ['N/A'] } },
};
const completo = () => ({ valor_unitario: '59.90', estrutura_tipo: 'camisa', tipo_impressao: 'N/A', estrutura: { malha: 'N/A', gola: 'N/A' } });

test('N/A é uma escolha válida, não um campo vazio', () => {
  assert.equal(validarModeloOp2(completo(), grupos), '');
});

test('conjunto copia campos compatíveis e a grade da camisa para o calção', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'], manga: ['CURTA', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'], acabamentos: ['CADARÇO', 'N/A'] } },
  };
  let configuracao = op2NovaConfiguracaoConjunto(opcoes);
  configuracao.camisa.estrutura.cor = 'AZUL';
  configuracao.camisa.outros.manga = 'Manga especial';
  configuracao.camisa.observacoes_campos.cor = 'Tom aprovado';
  configuracao.camisa.grades = ['1'];
  configuracao.camisa.gradePorGrade = { 1: { 9: 3 } };
  configuracao = op2CopiarCamisaParaCalcao(opcoes, configuracao);
  assert.equal(configuracao.calcao.estrutura.cor, 'AZUL');
  assert.equal(configuracao.calcao.outros.manga, 'Manga especial');
  assert.equal(configuracao.calcao.observacoes_campos.cor, 'Tom aprovado');
  assert.deepEqual(configuracao.calcao.gradePorGrade, { 1: { 9: 3 } });
  assert.equal(op2TotalComponenteConjunto(configuracao, 'calcao'), 3);
});

test('conjunto preserva como Outro uma gola da camisa ausente no catálogo do calção', () => {
  const opcoes = {
    camisa: { campos: { gola: ['CARECA', 'OUTRO', 'N/A'], tipo_impressao: ['SILK', 'N/A'] } },
    calcao: { campos: { gola: ['POLO', 'OUTRO', 'N/A'], tipo_impressao: ['SILK', 'N/A'] } },
  };
  let configuracao = op2NovaConfiguracaoConjunto(opcoes);
  configuracao.camisa.estrutura.gola = 'CARECA';
  configuracao.camisa.estrutura.tipo_impressao = ['SILK'];

  configuracao = op2CopiarCamisaParaCalcao(opcoes, configuracao);

  assert.equal(configuracao.calcao.estrutura.gola, 'OUTRO');
  assert.equal(configuracao.calcao.outros.gola, 'CARECA');
  assert.deepEqual(configuracao.calcao.estrutura.tipo_impressao, ['SILK']);
});

test('troca entre conjunto e peça individual preserva a estrutura preenchida', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'], gola: ['POLO', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'], bolso: ['LATERAL', 'N/A'] } },
  };
  const draft = {
    estrutura: { cor: 'AZUL', gola: 'POLO' },
    cor_personalizada: '', estrutura_outros: {}, estrutura_observacoes: {},
    configuracao_conjunto: op2NovaConfiguracaoConjunto(opcoes),
  };
  op2PreservarEstruturaAoTrocarTipo(opcoes, draft, 'camisa', 'conjunto');
  assert.equal(draft.configuracao_conjunto.camisa.estrutura.cor, 'AZUL');
  assert.equal(draft.configuracao_conjunto.calcao.estrutura.cor, 'AZUL');

  draft.configuracao_conjunto.camisa.estrutura.gola = 'POLO';
  draft.estrutura = {};
  op2PreservarEstruturaAoTrocarTipo(opcoes, draft, 'conjunto', 'camisa');
  assert.equal(draft.estrutura.cor, 'AZUL');
  assert.equal(draft.estrutura.gola, 'POLO');
});

test('troca de peça individual para conjunto preserva grade e quantidades', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'] } },
  };
  const draft = {
    estrutura: { cor: 'AZUL' }, tipo_impressao: ['N/A'],
    cor_personalizada: '', estrutura_outros: {}, estrutura_observacoes: {},
    grades: ['1'], gradePorGrade: { 1: { 9: 2, 10: 3 } },
    configuracao_conjunto: op2NovaConfiguracaoConjunto(opcoes),
  };

  op2PreservarEstruturaAoTrocarTipo(opcoes, draft, 'camisa', 'conjunto');

  assert.deepEqual(draft.configuracao_conjunto.camisa.grades, ['1']);
  assert.deepEqual(draft.configuracao_conjunto.camisa.gradePorGrade, { 1: { 9: 2, 10: 3 } });
  assert.deepEqual(draft.configuracao_conjunto.calcao.gradePorGrade, { 1: { 9: 2, 10: 3 } });
  draft.configuracao_conjunto.camisa.gradePorGrade[1][9] = 7;
  assert.equal(draft.configuracao_conjunto.calcao.gradePorGrade[1][9], 2);
});

test('troca de conjunto para peça individual preserva a grade do componente ativo', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'] } },
  };
  const configuracao = op2NovaConfiguracaoConjunto(opcoes);
  configuracao.calcao.grades = ['2'];
  configuracao.calcao.gradePorGrade = { 2: { 11: 4, 12: 2 } };
  const draft = {
    estrutura: {}, tipo_impressao: ['N/A'], quantidade: 1,
    cor_personalizada: '', estrutura_outros: {}, estrutura_observacoes: {},
    grades: [], gradePorGrade: {}, configuracao_conjunto: configuracao,
  };

  op2PreservarEstruturaAoTrocarTipo(opcoes, draft, 'conjunto', 'calcao', 'calcao');

  assert.deepEqual(draft.grades, ['2']);
  assert.deepEqual(draft.gradePorGrade, { 2: { 11: 4, 12: 2 } });
  assert.equal(draft.quantidade, 6);
});

test('valor visível prevalece sobre valor antigo ao entrar no conjunto', () => {
  const opcoes = {
    agasalho: { campos: { tipo_impressao: ['BORDADO', 'SUBLIMAÇÃO', 'N/A'] } },
    camisa: { campos: { tipo_impressao: ['BORDADO', 'SUBLIMAÇÃO', 'N/A'] } },
    calcao: { campos: { tipo_impressao: ['BORDADO', 'SUBLIMAÇÃO', 'N/A'] } },
  };
  const configuracao = op2NovaConfiguracaoConjunto(opcoes);
  configuracao.camisa.estrutura.tipo_impressao = ['SUBLIMAÇÃO'];
  configuracao.calcao.estrutura.tipo_impressao = ['SUBLIMAÇÃO'];
  const draft = {
    estrutura: {}, tipo_impressao: ['BORDADO'],
    estrutura_outros: {}, estrutura_observacoes: {}, cor_personalizada: '',
    configuracao_conjunto: configuracao,
  };

  op2PreservarEstruturaAoTrocarTipo(opcoes, draft, 'agasalho', 'conjunto');

  assert.deepEqual(draft.configuracao_conjunto.camisa.estrutura.tipo_impressao, ['BORDADO']);
  assert.deepEqual(draft.configuracao_conjunto.calcao.estrutura.tipo_impressao, ['BORDADO']);
});

test('conjunto exige duas fichas completas com totais iguais', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'] } },
  };
  const configuracao = op2NovaConfiguracaoConjunto(opcoes);
  for (const componente of ['camisa', 'calcao']) {
    configuracao[componente].grades = ['1'];
    configuracao[componente].gradePorGrade = { 1: { 9: 2 } };
  }
  assert.equal(validarConjuntoOp2(configuracao, opcoes), '');
  configuracao.calcao.gradePorGrade[1][9] = 1;
  assert.match(validarConjuntoOp2(configuracao, opcoes), /mesmo total/);
});

test('conjunto pode ser incluído antes de definir a grade', () => {
  const opcoes = {
    camisa: { campos: { cor: ['AZUL', 'N/A'] } },
    calcao: { campos: { cor: ['AZUL', 'N/A'] } },
  };
  const configuracao = op2NovaConfiguracaoConjunto(opcoes);
  assert.equal(validarConjuntoOp2(configuracao, opcoes), '');
});
test('valor vazio, negativo e inválido são recusados', () => {
  for (const valor of ['', null, '-1', 'NaN', 'Infinity', 'abc', '1.001', '10000000000']) {
    assert.match(validarModeloOp2({ ...completo(), valor_unitario: valor }, grupos), /Valor unitário/);
  }
});
test('preço zero, positivo e centavos são aceitos', () => {
  for (const valor of [0, '0', '0.01', '10', 20.9]) assert.equal(validarModeloOp2({ ...completo(), valor_unitario: valor }, grupos), '');
});
test('todos os campos visíveis são obrigatórios', () => {
  for (const campo of ['malha', 'gola']) {
    const draft = completo();
    draft.estrutura[campo] = '';
    assert.match(validarModeloOp2(draft, grupos), /obrigatório/);
  }
  assert.match(validarModeloOp2({ ...completo(), tipo_impressao: '' }, grupos), /obrigatório/);
});
test('valores desconhecidos não substituem N/A', () => {
  const draft = completo();
  draft.estrutura.gola = 'INVENTADO';
  assert.match(validarModeloOp2(draft, grupos), /opção válida/);
});
test('campos de outro tipo não bloqueiam o tipo atual', () => {
  const draft = completo();
  draft.estrutura_tipo = 'colete';
  delete draft.estrutura.gola;
  assert.equal(validarModeloOp2(draft, grupos), '');
});
test('tipo de peça desconhecido é recusado', () => {
  assert.match(validarModeloOp2({ ...completo(), estrutura_tipo: '' }, grupos), /tipo de peça válido/);
});

test('impressão e acabamento aceitam múltiplas opções válidas', () => {
  const gruposMultiplos = {
    calcao: {label: 'Calção', campos: {tipo_impressao: ['SILK', 'RELEVO'], acabamentos: ['RECORTE', 'FORRO']}},
  };
  const erro = validarModeloOp2({
    valor_unitario: '10', estrutura_tipo: 'calcao',
    tipo_impressao: ['SILK', 'RELEVO'], estrutura: {acabamentos: ['RECORTE', 'FORRO']},
  }, gruposMultiplos);
  assert.equal(erro, '');
  assert.equal(validarModeloOp2({
    valor_unitario: '10', estrutura_tipo: 'calcao',
    tipo_impressao: 'SILK, RELEVO', estrutura: {acabamentos: 'RECORTE + FORRO'},
  }, gruposMultiplos), '');
});

test('Outro exige descrição e observações não interferem na validação', () => {
  const opcoes = {camisa: {label: 'Camisa', campos: {tipo_impressao: ['OUTRO', 'N/A'], malha: ['OUTRO', 'N/A']}}};
  const draft = {valor_unitario: '10', estrutura_tipo: 'camisa', tipo_impressao: ['N/A'], estrutura: {malha: 'OUTRO'}, estrutura_outros: {}, estrutura_observacoes: {malha: 'Detalhe'}};
  assert.match(validarModeloOp2(draft, opcoes), /descreva/);
  draft.estrutura_outros.malha = 'Neoprene';
  assert.equal(validarModeloOp2(draft, opcoes), '');
});

test('seletor múltiplo mantém N/A exclusivo e resume escolhas', () => {
  let valores = op2AlternarMultisselecao([], 'SILK', true);
  valores = op2AlternarMultisselecao(valores, 'RELEVO', true);
  assert.deepEqual(valores, ['SILK', 'RELEVO']);
  assert.equal(op2ResumoMultisselecao(valores), 'SILK, RELEVO');
  assert.deepEqual(op2AlternarMultisselecao(valores, 'N/A', true), ['N/A']);
  assert.deepEqual(op2AlternarMultisselecao(['N/A'], 'BORDADO', true), ['BORDADO']);
});

test('estrutura inicia N/A em todos os campos, inclusive multisseleção', () => {
  const estrutura = op2EstruturaPadraoNaoMultipla({
    camisa: { campos: {
      tipo_impressao: ['SILK', 'N/A'], cor: ['PRETO', 'N/A'],
      acabamentos: ['VIES', 'N/A'], manga: ['CURTA', 'N/A'],
    } },
  }, 'camisa', {});
  assert.deepEqual(estrutura, {
    tipo_impressao: ['N/A'], cor: 'N/A', acabamentos: ['N/A'], manga: 'N/A',
  });
});

test('estrutura mantém escolhas existentes ao preencher os padrões', () => {
  const estrutura = op2EstruturaPadraoNaoMultipla({
    camisa: { campos: { cor: ['PRETO', 'N/A'], manga: ['CURTA', 'N/A'] } },
  }, 'camisa', { cor: 'PRETO' });
  assert.deepEqual(estrutura, { cor: 'PRETO', manga: 'N/A' });
});

test('rascunho recuperado troca somente identificadores de itens duplicados', () => {
  const gerados = ['novo-2', 'novo-3'];
  const itens = op2NormalizarUidsItens([
    { uid: 'mesmo', nome: 'Primeiro', quantidade: 13 },
    { uid: 'mesmo', nome: 'Segundo', quantidade: 13 },
    { nome: 'Terceiro', quantidade: 2 },
  ], () => gerados.shift());

  assert.deepEqual(itens.map(item => item.uid), ['mesmo', 'novo-2', 'novo-3']);
  assert.deepEqual(itens.map(item => item.nome), ['Primeiro', 'Segundo', 'Terceiro']);
  assert.deepEqual(itens.map(item => item.quantidade), [13, 13, 2]);
});
