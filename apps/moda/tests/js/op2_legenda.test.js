const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../../../../static/js/op2_legenda.js'), 'utf8');

function editor(fetch, texto = 'Original') {
  const timers = new Map();
  let id = 0;
  const context = vm.createContext({
    fetch, URLSearchParams, AbortController,
    setTimeout(fn, delay) { timers.set(++id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(source, context);
  const state = context.op2LegendaAutomatica();
  state.$refs = {
    texto: { value: texto },
    form: { action: '/acao/', elements: {
      visual_id: { value: '42' }, csrfmiddlewaretoken: { value: 'csrf-test' },
    } },
  };
  state.$dispatch = () => {};
  state.init();
  return { state, timers };
}
const ok = descricao => ({ ok: true, json: async () => ({ ok: true, descricao }) });

test('digitação rápida é agrupada e apagar também é salvo', async () => {
  const enviados = [];
  const { state, timers } = editor(async (_, opts) => {
    enviados.push(opts.body.get('descricao'));
    assert.equal(opts.body.get('csrfmiddlewaretoken'), 'csrf-test');
    return ok(opts.body.get('descricao').trim());
  });
  for (const texto of ['F', 'Fr', 'Frente']) {
    state.$refs.texto.value = texto; state.agendar();
  }
  assert.equal(timers.size, 1);
  await [...timers.values()][0].fn();
  assert.deepEqual(enviados, ['Frente']);
  state.$refs.texto.value = '';
  await state.salvar();
  assert.deepEqual(enviados, ['Frente', '']);
  assert.equal(state.salvo, '');
});

test('resposta lenta preserva nova digitação e envia a versão mais recente depois', async () => {
  let concluir;
  const enviados = [];
  const { state, timers } = editor(async (_, opts) => {
    const texto = opts.body.get('descricao'); enviados.push(texto);
    if (enviados.length === 1) return new Promise(resolve => { concluir = () => resolve(ok(texto)); });
    return ok(texto);
  });
  state.$refs.texto.value = 'Frente';
  const primeiro = state.salvar();
  state.$refs.texto.value = 'Verso';
  await state.salvar();
  assert.deepEqual(enviados, ['Frente']);
  concluir(); await primeiro;
  assert.equal(state.$refs.texto.value, 'Verso');
  await [...timers.values()][0].fn();
  assert.deepEqual(enviados, ['Frente', 'Verso']);
  assert.equal(state.salvo, 'Verso');
});

test('erro de rede mantém texto, alerta ao sair e permite tentar novamente', async () => {
  let falhar = true;
  const { state } = editor(async () => { if (falhar) throw new Error('offline'); return ok('Frente'); });
  state.$refs.texto.value = 'Frente';
  await state.salvar();
  assert.equal(state.estado, 'erro');
  assert.equal(state.salvo, 'Original');
  assert.equal(state.$refs.texto.value, 'Frente');
  let alertou = false;
  state.avisarSaida({ preventDefault() { alertou = true; } });
  assert.equal(alertou, true);
  falhar = false; await state.salvar();
  assert.equal(state.estado, 'salvo');
});

test('login redirecionado e erros HTTP não aparecem como salvos', async () => {
  for (const resposta of [{ ok: true, redirected: true }, { ok: false }, { ok: true, json: async () => ({}) }]) {
    const { state } = editor(async () => resposta);
    state.$refs.texto.value = 'Novo'; await state.salvar();
    assert.equal(state.estado, 'erro');
    assert.equal(state.salvo, 'Original');
  }
});

test('visões desktop/mobile sincronizam apenas campos que não estão sendo editados', () => {
  const { state } = editor(async () => ok(''));
  state.sincronizar({ id: '42', texto: 'Frente' });
  assert.equal(state.$refs.texto.value, 'Frente');
  state.$refs.texto.value = 'Texto em digitação';
  state.sincronizar({ id: '42', texto: 'Verso' });
  assert.equal(state.$refs.texto.value, 'Texto em digitação');
});
