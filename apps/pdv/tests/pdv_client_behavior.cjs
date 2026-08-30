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
  for (const tipo of ['boleto', 'credito', 'debito', 'pix', 'dinheiro', 'transferencia', 'cheque', 'vale']) {
    assert.equal(app.corFormaPgtoIconNovo({tipo}),
      `background:var(--pdv-pgto-${tipo}-bg);color:var(--pdv-pgto-${tipo}-fg);border:1px solid var(--pdv-pgto-${tipo}-bd);`);
  }
  assert.match(app.corFormaPgtoIconNovo({tipo: 'desconhecido'}), /--pdv-pgto-outro-bg/);
  for (const tipo of ['ted', 'doc', 'deposito_em_conta']) {
    assert.equal(app.svgFormaPgto({tipo}), app.svgFormaPgto({tipo: 'transferencia'}));
    assert.equal(app.corFormaPgtoIconNovo({tipo}), app.corFormaPgtoIconNovo({tipo: 'transferencia'}));
  }
  const editor = create();
  editor.$nextTick = fn => fn();
  editor.$refs = {clientEditor:{showModal(){}, close(){}, querySelector(){return null;}}, clientNameButton:{focus(){}}};
  editor.venda.cliente = {id:15,nome:'Antes',tabela_preco_id:3};
  editor.venda.itens = [{id:4,quantidade:2,preco:10}];
  editor.venda.pagamentos = [{valor:5}];
  const saleBefore = JSON.stringify([editor.venda.itens,editor.venda.pagamentos]);
  respond({dados:{razao_social:'Antes',tipo_pessoa:'F'}});
  await editor.abrirEdicaoCliente();
  assert.equal(editor.showModalEditarCliente,true);
  assert.equal(editor.temModalAberto(),true);
  context.csrf = () => 'local-test';
  respond({erro:'CPF inválido',campos:{cpf_cnpj:['Inválido']}},false);
  await editor.salvarEdicaoCliente();
  assert.equal(editor.showModalEditarCliente,true);
  assert.equal(editor.clienteEdicaoErro,'CPF inválido');
  respond({cliente:{id:15,razao_social:'Depois',cpf_cnpj:'12345678901'}});
  await editor.salvarEdicaoCliente();
  assert.equal(editor.venda.cliente.nome,'Depois');
  assert.equal(editor.venda.cliente.tabela_preco_id,3);
  assert.equal(JSON.stringify([editor.venda.itens,editor.venda.pagamentos]),saleBefore);
  assert.equal(editor.showModalEditarCliente,false);
  context.fetch=()=>new Promise(resolve=>{ release=resolve; });
  const pendingEditor=editor.abrirEdicaoCliente();
  editor.fecharEdicaoCliente();
  release({ok:true,json:async()=>({dados:{razao_social:'Atrasado'}})});
  await pendingEditor;
  assert.equal(editor.showModalEditarCliente,false);
  assert.notEqual(editor.clienteEdicao.razao_social,'Atrasado');
  let prevented=false,stopped=false;
  editor.teclaEdicaoCliente({key:'F12',preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
  assert.ok(prevented && stopped);
  editor.showModalEditarCliente=true;
  editor.teclaEdicaoCliente({key:'Escape',preventDefault(){},stopPropagation(){}});
  assert.equal(editor.showModalEditarCliente,false);
  console.log('PDV: initialization, promotion tags, icons and client editor OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
