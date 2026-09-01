import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const template = readFileSync(new URL('../templates/moda/op2_create.html', import.meta.url), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const modeloValidation = readFileSync(
  new URL('../../../static/js/op2_modelo_validacao.js', import.meta.url), 'utf8',
);
const clientesScript = readFileSync(
  new URL('../../../static/js/op2_clientes.js', import.meta.url), 'utf8',
);

function workspace() {
  const alerts = [];
  const json = {
    'op2-clientes': [], 'op2-modelos-grade': {},
    'op2-grades': [{ id: 'adulto', nome: 'Adulto', tamanhos: ['M'] }],
    'op2-tamanhos-labels': { M: 'M' },
    'op2-estrutura-opcoes': {
      camisa: { label: 'Camisa', campos: { tipo_impressao: ['N/A'], malha: ['N/A'] } },
    },
  };
  const state = vm.runInNewContext(`${modeloValidation};${clientesScript};${script};op2NovaMelhorada()`, {
    document: { getElementById: id => ({ textContent: JSON.stringify(json[id]) }) },
    alert: message => alerts.push(message),
  });
  state.$nextTick = () => {};
  state.clienteId = 'cliente';
  state.itens = ['primeiro', 'segundo'].map(uid => ({
    uid, nome: uid, produto_id: 'mesmo-modelo', grade_id: 'adulto',
    grade_nome: 'Adulto', grades: ['adulto'], quantidade: 2, valor_unitario: 10,
    estrutura_tipo: 'camisa', tipo_impressao: 'N/A', estrutura: { malha: 'N/A' },
    gradeQuantidades: [{ tamanho_id: 'M', quantidade: 2 }],
    gradePorGrade: { adulto: { M: 2 } },
  }));
  return { state, alerts };
}

function pessoa(state, itemUid, nome = 'ANA') {
  state.adicionarLinhaPersonalizacao(itemUid);
  const row = state.personalizacoes.at(-1);
  Object.assign(row, { tamanhoId: 'M', nome, numero: '10' });
  return row;
}

function submit(state) {
  const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  state.validarAntesDeSalvar(event);
  return event;
}

function pagamento(state) {
  Object.assign(state.pagamentos[0], {
    forma: 'nao_informado', valor: state.totalValor(), valorEditado: false,
  });
}

test('o rascunho já tem estrutura e grades ao iniciar a tela', () => {
  const { state } = workspace();
  assert.equal(state.draft.grades.length, 0);
  assert.deepEqual({ ...state.draft.estrutura }, { malha: 'N/A' });
  assert.equal(state.totalTodasGrades(), 0);
});

test('cada produto tem sua própria lista, mesmo usando o mesmo modelo', () => {
  const { state } = workspace();
  pessoa(state, 'primeiro');
  pessoa(state, 'segundo', 'BIA');
  assert.equal(state.personalizacoesDoItem('primeiro')[0].nome, 'ANA');
  assert.equal(state.personalizacoesDoItem('segundo')[0].nome, 'BIA');
  assert.equal(state.vagasOrcamento('primeiro')[0].restam, 1);
  assert.equal(state.vagasOrcamento('segundo')[0].restam, 1);
});

test('reservas da grade são por produto e excluem a própria linha do seletor', () => {
  const { state } = workspace();
  const row = pessoa(state, 'primeiro');
  pessoa(state, 'primeiro', 'BIA');
  assert.equal(state.vagasOrcamento('primeiro')[0].restam, 0);
  assert.equal(state.vagasOrcamento('primeiro', row.uid)[0].restam, 1);
  assert.equal(state.vagasOrcamento('segundo')[0].restam, 2);
  state.removerPersonalizacao(row.uid);
  assert.equal(state.vagasOrcamento('primeiro')[0].restam, 1);
});

test('excluir um produto remove só suas pessoas e recalcula o índice de envio', () => {
  const { state } = workspace();
  pessoa(state, 'primeiro');
  pessoa(state, 'segundo', 'BIA');
  state.removerItem(0);
  assert.equal(state.personalizacoes.length, 1);
  assert.equal(state.personalizacoes[0].nome, 'BIA');
  assert.equal(state.indiceItem(state.personalizacoes[0].itemUid), 0);
});

test('as linhas preenchidas vão direto no salvar, sem uma etapa extra', () => {
  const { state, alerts } = workspace();
  pagamento(state);
  pessoa(state, 'primeiro');
  pessoa(state, 'segundo');
  state.adicionarLinhaPersonalizacao('segundo');
  assert.equal(state.personalizacoesPreenchidas().length, 2);
  assert.equal(submit(state).defaultPrevented, false);
  assert.equal(alerts.length, 0);
});

test('pagamento começa sem forma e o total dos itens alimenta o primeiro valor', () => {
  const { state } = workspace();
  assert.equal(state.pagamentos[0].forma, '');
  assert.equal(state.pagamentos[0].valorEditado, false);
  assert.equal(state.totalValor(), 40);
  assert.match(template, /pagamento\.valor=totalValor\(\)/);
  assert.match(template, /required><option value="">Selecione a forma de pagamento/);
});

test('forma de pagamento vazia bloqueia o orçamento', () => {
  const { state, alerts } = workspace();
  state.pagamentos[0].valor = state.totalValor();
  assert.equal(submit(state).defaultPrevented, true);
  assert.match(alerts.at(-1), /forma de pagamento/);
});

test('linhas incompletas e excesso de personalizações bloqueiam o envio', () => {
  const { state, alerts } = workspace();
  const row = pessoa(state, 'primeiro');
  row.tamanhoId = '';
  assert.equal(submit(state).defaultPrevented, true);
  assert.match(alerts.at(-1), /Complete.*primeiro/);
  row.tamanhoId = 'M';
  pessoa(state, 'primeiro');
  pessoa(state, 'primeiro');
  assert.equal(submit(state).defaultPrevented, true);
  assert.match(alerts.at(-1), /ultrapassam/);
});

test('edição preserva o vínculo e não reduz a grade abaixo das pessoas informadas', () => {
  const { state, alerts } = workspace();
  pessoa(state, 'primeiro');
  pessoa(state, 'primeiro');
  state.editarItem(0);
  state.draft.gradePorGrade.adulto.M = 1;
  state.draft.quantidade = 1;
  state.salvarDraft();
  assert.match(alerts.at(-1), /não comporta/);
  assert.equal(state.itens[0].quantidade, 2);
  state.draft.gradePorGrade.adulto.M = 3;
  state.draft.quantidade = 3;
  state.salvarDraft();
  assert.equal(state.itens[0].uid, 'primeiro');
  assert.equal(state.itens[0].quantidade, 3);
  assert.equal(state.personalizacoesDoItem('primeiro').length, 2);
});
