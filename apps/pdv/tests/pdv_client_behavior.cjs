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
  const discount = create();
  discount.$nextTick = fn => fn();
  discount.$refs = {};
  const item = {_id:1,quantidade:2,valor_unitario:100,desconto_valor:0,valor_total:200};
  discount.venda.itens=[item];
  discount.recalcularTotais();
  discount.abrirDescontoItem(item);
  const draft=discount.descontoItemRascunho;
  discount.sincronizarDesconto(draft,'percentual','12,5',discount.baseDescontoItem());
  assert.equal(draft.valor,'25,00');
  assert.equal(item.desconto_valor,0,'Editing the draft must not change the sale');
  discount.sincronizarDesconto(draft,'valor','50',discount.baseDescontoItem());
  assert.equal(draft.percentual,'25,00');
  discount.aplicarDescontoItem(item,draft.valor);
  assert.equal(item.valor_total,150);
  assert.equal(item.desconto_percentual,25);
  discount.abrirDescontoItem(item);
  assert.equal(draft.valor,'50,00');
  discount.sincronizarDesconto(draft,'percentual','12,',200);
  assert.equal(draft.percentual,'12,','Do not erase decimal separator while typing');
  assert.equal(draft.valor,'24,00');
  discount.abrirDescontoGeral();
  const general=discount.descontoGeralRascunho;
  discount.sincronizarDesconto(general,'percentual','10',discount.venda.subtotal);
  assert.equal(general.valor,'15,00','General discount is based on net item subtotal');
  discount.aplicarDescontoGeralValor();
  assert.equal(discount.venda.total,135);
  discount.abrirDescontoGeral();
  assert.equal(general.percentual,'10,00');
  discount.sincronizarDesconto(general,'valor','30',discount.venda.subtotal);
  assert.equal(general.percentual,'20,00');
  discount.aplicarDescontoGeralValor();
  assert.equal(discount.venda.total,120);
  for (const [field,input,base,expected] of [
    ['percentual','150',200,'200,00'],['percentual','-5',200,'0,00'],
    ['percentual','10',0,'0,00'],['percentual','12,5',89.99,'11,25'],
    ['percentual','12,5',80.60,'10,08'],
    ['percentual','abc',100,'0,00'],['valor','1000',200,'100,00'],
    ['valor','-2',200,'0,00'],['valor','10',0,'0,00'],
    ['valor','1.234,56',2000,'61,73'],['valor','25.50',100,'25,50'],
  ]) {
    discount.sincronizarDesconto(draft,field,input,base);
    assert.equal(draft[field==='valor'?'percentual':'valor'],expected,`${field} ${input}`);
  }
  discount.aplicarDescontoItem(item,'999');
  assert.equal(item.desconto_valor,200);
  assert.equal(item.valor_total,0);
  discount.aplicarDescontoItemPct(item,0);
  assert.equal(item.desconto_valor,0);
  discount.aplicarDescontoGeral(0);
  assert.equal(discount.venda.total,200);
  item.valor_unitario=20;
  discount.abrirDescontoItem(item);
  discount.sincronizarDesconto(draft,'percentual','10',discount.baseDescontoItem());
  assert.equal(draft.valor,'4,00','Discount uses the manually edited current price');
  discount.aplicarDescontoItem(item,40);
  item.quantidade=1;
  discount.recalcularItem(item);
  assert.equal(item.desconto_valor,20,'Persist the capped discount after quantity changes');
  discount.aplicarDescontoGeral(10);
  discount.removerItem(item);
  assert.equal(discount.venda.total,0,'Removing all items must not leave a negative sale');

  const share=create();
  let nativeShare=0, downloaded=0, opened=0, popupClosed=0, url='';
  context.navigator={userAgent:'Windows',canShare:()=>true,share:async()=>{nativeShare++;}};
  context.window.prompt=()=>'(55) 99999-1234';
  context.window.open=()=>{opened++;return {opener:{},location:{replace(value){url=value;}},close(){popupClosed++;}};};
  share._dadosCupomVenda=async()=>({numero:1,total:100});
  share._gerarCupomPdfBlob=async()=>({});
  share._baixarBlob=()=>{downloaded++;};
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(nativeShare,0,'Desktop must not open the Windows share dialog');
  assert.equal(downloaded,1);
  assert.equal(opened,1);
  const wa=new URL(url);
  assert.equal(wa.origin,'https://web.whatsapp.com');
  assert.equal(wa.searchParams.get('phone'),'5555999991234');
  assert.match(wa.searchParams.get('text'),/100,00/);
  context.window.prompt=()=>null;
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(opened,1,'Cancel must not download or open a tab');
  assert.equal(downloaded,1);
  context.window.prompt=()=>'';
  share._gerarCupomPdfBlob=async()=>{throw new Error('pdf');};
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(popupClosed,1);
  assert.equal(share.compartilhandoCupom,false);
  const receipt=create();
  const cartBefore=JSON.stringify(receipt.venda);
  let receiptHtml='', receiptClosed=0;
  context.URL=URL;
  context.window.location.origin='https://ited.app.br';
  context.window.open=()=>({document:{open(){},write(value){receiptHtml=value;},close(){}},focus(){},close(){receiptClosed++;}});
  respond({ok:true,numero_venda:1,cliente_nome:'João & Maria',itens:[],pagamentos:[],valor_total:100});
  await receipt.visualizarComprovante(1);
  assert.match(receiptHtml,/João &amp; Maria/);
  assert.match(receiptHtml,/Imprimir em A4/);
  assert.equal(JSON.stringify(receipt.venda),cartBefore,'Viewing a receipt must not replace the active cart');
  respond({erro:'Venda não encontrada'},false);
  await receipt.visualizarComprovante(99);
  assert.equal(receiptClosed,1);

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
