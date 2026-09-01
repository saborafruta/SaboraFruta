const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  validarModeloOp2, op2AlternarMultisselecao, op2ResumoMultisselecao,
} = require('../../../../static/js/op2_modelo_validacao.js');

const grupos = {
  camisa: { label: 'Camisa', campos: { tipo_impressao: ['SILK', 'N/A'], malha: ['PP', 'N/A'], gola: ['POLO', 'N/A'] } },
  colete: { label: 'Colete', campos: { tipo_impressao: ['N/A'], malha: ['N/A'] } },
};
const completo = () => ({ valor_unitario: '59.90', estrutura_tipo: 'camisa', tipo_impressao: 'N/A', estrutura: { malha: 'N/A', gola: 'N/A' } });

test('N/A é uma escolha válida, não um campo vazio', () => {
  assert.equal(validarModeloOp2(completo(), grupos), '');
});
test('valor vazio, zero, negativo e inválido são recusados', () => {
  for (const valor of ['', null, 0, '0', '-1', 'NaN', 'Infinity', 'abc', '1.001', '10000000000']) {
    assert.match(validarModeloOp2({ ...completo(), valor_unitario: valor }, grupos), /Valor unitário/);
  }
});
test('preço positivo e centavos são aceitos', () => {
  for (const valor of ['0.01', '10', 20.9]) assert.equal(validarModeloOp2({ ...completo(), valor_unitario: valor }, grupos), '');
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
});

test('seletor múltiplo mantém N/A exclusivo e resume escolhas', () => {
  let valores = op2AlternarMultisselecao([], 'SILK', true);
  valores = op2AlternarMultisselecao(valores, 'RELEVO', true);
  assert.deepEqual(valores, ['SILK', 'RELEVO']);
  assert.equal(op2ResumoMultisselecao(valores), 'SILK, RELEVO');
  assert.deepEqual(op2AlternarMultisselecao(valores, 'N/A', true), ['N/A']);
  assert.deepEqual(op2AlternarMultisselecao(['N/A'], 'BORDADO', true), ['BORDADO']);
});
