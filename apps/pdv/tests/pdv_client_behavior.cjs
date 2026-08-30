// Tests the actual Django-rendered component, not a second implementation.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let intervals = 0;
const context = vm.createContext({
  setInterval: () => { intervals++; },
  URLSearchParams, Date,
  window: { location: { search: '' } },
});
vm.runInContext(fs.readFileSync(0, 'utf8'), context);
const create = () => {
  const app = context.pdv();
  app.toast_ = () => {};
  return app;
};
const respond = (payload, ok = true) => {
  context.fetch = async () => ({ok, json: async () => payload});
};
(async () => {
  const app = create();
  let release;
  context.fetch = () => new Promise(resolve => { release = resolve; });
  const loading = app.carregarEstado();
  assert.equal(app.sessaoCarregada, false);
  assert.equal(app.showModalCaixa, false);
  release({ok: true, json: async () => ({sessao: {id: 7}})});
  await loading;
  assert.equal(app.sessaoCarregada, true);
  assert.equal(app.sessao.id, 7);
  assert.equal(app.showModalCaixa, false);

  const closed = create();
  respond({sessao: null, formas_pagamento: []});
  await closed.carregarEstado();
  assert.equal(closed.sessaoCarregada, true);
  assert.equal(closed.sessao, null);
  assert.equal(closed.showModalCaixa, false);
  await closed.caixaMenuAcao('abrir');
  assert.equal(closed.showModalCaixa, true);

  for (const [payload, ok] of [[{}, false], [{}, true]]) {
    const failed = create();
    respond(payload, ok);
    await failed.carregarEstado();
    assert.equal(failed.sessaoCarregada, false);
    assert.ok(failed.erroEstado);
    await failed.caixaMenuAcao('abrir');
    assert.equal(failed.showModalCaixa, false);
    respond({sessao: {id: 8}});
    await failed.carregarEstado();
    assert.equal(failed.sessao.id, 8);
    assert.equal(failed.erroEstado, '');
  }

  const initial = create();
  let focus = 0, shortcuts = 0, loads = 0, complete;
  initial.$nextTick = fn => fn();
  initial.focarBuscaProduto = () => { focus++; };
  initial.registrarAtalhos = () => { shortcuts++; };
  initial.carregarEstado = () => { loads++; return new Promise(resolve => { complete = resolve; }); };
  initial.atualizarListasTopo = async () => {};
  initial.aplicarClienteInicial = async () => {};
  initial.aplicarComandaInicial = async () => {};
  const initializing = initial.init();
  assert.equal(focus, 1, 'Search focus must not wait for network');
  await initial.init();
  assert.equal(loads, 1);
  assert.equal(shortcuts, 1);
  assert.equal(intervals, 1);
  complete();
  await initializing;
  assert.equal(focus, 1, 'Do not steal focus after network finishes');

  for (const tipo of ['normal', 'tabela_cliente', undefined]) {
    assert.equal(app.temPromocao({oferta_tipo: tipo, oferta_tag: 'PREÇO NORMAL', oferta_nome: 'Preço normal'}), false);
  }
  for (const tipo of ['promocional', 'categoria', 'combo', 'kit', 'brinde']) {
    assert.equal(app.temPromocao({oferta_tipo: tipo}), true);
    assert.equal(app.temPromocao({preco_origem_tipo: tipo}), true);
  }
  assert.equal(app.temPromocao({oferta_tipo: 'normal', preco_origem_tipo: 'promocional'}), false);
  assert.match(app.svgFormaPgto({tipo: 'pix'}), /M4 4h6v6H4z/);
  for (const tipo of ['ted', 'doc', 'deposito_em_conta']) {
    assert.equal(app.svgFormaPgto({tipo}), app.svgFormaPgto({tipo: 'transferencia'}));
    assert.equal(app.corFormaPgtoIconNovo({tipo}), app.corFormaPgtoIconNovo({tipo: 'transferencia'}));
  }
  console.log('PDV: initialization, loading, retry, promotion tags and icons OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
