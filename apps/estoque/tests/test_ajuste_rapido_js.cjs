// Testa os handlers reais do template sem navegador ou acesso ao servidor.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../templates/estoque/estoque/ajuste_rapido.html'), 'utf8');
const script = html.slice(html.indexOf('    function parseQty'), html.indexOf('    if (filterForm && barcodeInput)'));

function element(data = {}) {
  const listeners = {};
  const classes = new Set();
  return {
    dataset: data, value: '', disabled: false, textContent: '',
    classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x), toggle: (x, yes) => yes ? classes.add(x) : classes.delete(x) },
    addEventListener: (event, fn) => { listeners[event] = fn; },
    emit(event, extra = {}) { return listeners[event]?.({ preventDefault() {}, ...extra }); },
    blur() { this.emit('blur'); },
  };
}

function setup() {
  const row = element({ productId: '586', current: '3', updateUrl: '/test' });
  const qty = element(); qty.value = '3';
  const plus = element({ step: '1' }), minus = element({ step: '-1' });
  const check = element(), status = element(), display = element();
  const controls = [qty, plus, minus, check];
  row.querySelector = key => ({ '[data-qty]': qty, '[data-check]': check, '[data-status]': status, '[data-current-display]': display })[key];
  row.querySelectorAll = key => key === '[data-step]' ? [plus, minus] : controls;
  let timerId = 0;
  const timers = new Map(), requests = [];
  const context = vm.createContext({
    FormData, Promise, Number, String, Date, Math,
    document: { cookie: '', querySelector: () => null, querySelectorAll: () => [row] },
    window: { setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id) },
    editedCounter: null, checkedCounter: null,
    fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
  });
  vm.runInContext(script, context);
  return { row, qty, plus, minus, check, status, requests, controls, context,
    async tick() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(fn => fn()); await Promise.resolve(); },
    type(value) { qty.value = value; qty.emit('input'); },
    async respond(value, ok = true) {
      const request = requests.at(-1);
      request.resolve({ ok, status: ok ? 200 : 409, headers: { get: () => 'application/json' }, json: async () => ({ ok, quantidade_atual: value, quantidade_atual_display: value, movimento_id: 1, error: 'Saldo mudou' }) });
      await row._stockSaving;
    },
  };
}

test('digitar 10 envia saldo final 10; Enter/blur/Conferir não duplicam', async () => {
  const ui = setup();
  ui.type('10'); ui.qty.emit('keydown', { key: 'Enter' }); ui.check.emit('click');
  await ui.tick();
  assert.equal(ui.requests.length, 1);
  const body = ui.requests[0].options.body;
  assert.equal(body.get('quantidade'), '10');
  assert.equal(body.get('saldo_exibido'), '3');
  assert.equal(body.get('interacao'), 'digitacao');
  assert.ok(ui.controls.every(control => control.disabled));
  await ui.respond('10');
  assert.equal(ui.qty.value, '10');
  assert.equal(ui.row.dataset.current, '10');
  assert.ok(ui.controls.every(control => !control.disabled));
  ui.qty.blur(); await ui.tick();
  assert.equal(ui.requests.length, 1);
});

test('botões acumulam cliques antes do envio; blur cancela timer anterior', async () => {
  const ui = setup();
  ui.plus.emit('click'); ui.plus.emit('click'); ui.minus.emit('click'); ui.qty.blur();
  await ui.tick();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.body.get('quantidade'), '4');
  assert.equal(ui.requests[0].options.body.get('interacao'), 'botoes');
  await ui.respond('4');
});

test('campo vazio ou inválido nunca vira ajuste para zero', async () => {
  for (const value of ['', ' ', 'abc', '-1', 'Infinity', 'NaN', '1000000000']) {
    const ui = setup(); ui.type(value); ui.qty.blur(); await ui.tick();
    assert.equal(ui.requests.length, 0, value);
    assert.ok(ui.status.classList.contains('is-error'), value);
  }
});

test('digitar cancela o debounce dos botões até sair do campo', async () => {
  const ui = setup(); ui.plus.emit('click'); ui.type('10'); await ui.tick();
  assert.equal(ui.requests.length, 0);
  ui.qty.blur(); await ui.tick();
  assert.equal(ui.requests[0].options.body.get('quantidade'), '10');
  await ui.respond('10');
});

test('zero explícito é aceito e registra interação mista', async () => {
  const ui = setup(); ui.type('1'); ui.minus.emit('click'); await ui.tick();
  assert.equal(ui.requests[0].options.body.get('quantidade'), '0');
  assert.equal(ui.requests[0].options.body.get('interacao'), 'mista');
  await ui.respond('0');
});

test('conflito não exibe conferido nem troca saldo e permite tentar novamente', async () => {
  const ui = setup(); ui.type('10'); ui.qty.blur(); await ui.tick(); await ui.respond('4', false);
  assert.equal(ui.row.dataset.current, '3');
  assert.equal(ui.qty.value, '10');
  assert.equal(ui.check.textContent, 'Conferir');
  assert.ok(ui.status.classList.contains('is-error'));
  assert.ok(ui.controls.every(control => !control.disabled));
});
