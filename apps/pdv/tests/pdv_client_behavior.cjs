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
  const cancellation = create();
  cancellation.$nextTick = fn => fn();
  cancellation.$refs = {};
  cancellation.historicoCliente = [{id:20}];
  cancellation.historicoVendas = [{id:20,status:'finalizada'}];
  cancellation.abrirCancelamentoHistorico(cancellation.historicoVendas[0]);
  Object.assign(cancellation.cancelamentoHistorico,{email:'admin@example.com',senha:'teste',motivo:'Cliente desistiu da compra.'});
  context.csrf = () => 'local-test';
  respond({erro:'Senha inválida'},false);
  await cancellation.confirmarCancelamentoHistorico();
  assert.equal(cancellation.cancelamentoHistorico.senha,'');
  assert.equal(cancellation.cancelamentoHistorico.erro,'Senha inválida');
  assert.equal(cancellation.historicoVendas.length,1);
  cancellation.cancelamentoHistorico.senha='teste';
  let cancellationBody;
  context.fetch = async (url,options) => {
    if(options) { cancellationBody=JSON.parse(options.body); return {ok:true,json:async()=>({ok:true})}; }
    return {ok:true,json:async()=>({vendas:[{id:20,status:'cancelada',cancelado_por:'Operador',autorizado_por:'Admin'}]})};
  };
  await cancellation.confirmarCancelamentoHistorico();
  assert.equal(cancellationBody.admin_senha,'teste');
  assert.equal(cancellation.cancelamentoHistorico.venda,null);
  assert.equal(cancellation.cancelamentoHistorico.senha,'');
  assert.equal(cancellation.historicoVendas[0].status,'cancelada');
  assert.equal(cancellation.historicoVendas[0].cancelado_por,'Operador');
  cancellation.abrirCancelamentoHistorico(cancellation.historicoVendas[0]);
  assert.equal(cancellation.cancelamentoHistorico.venda,null,'Cannot cancel twice');
  const offers = create();
  const product = {id:9, descricao:'Camisa', preco:100, preco_base:100, estoque_disponivel:5, tipo_produto:'unitario', ofertas:[
    {tipo:'normal',oferta_tipo:'normal',preco:100,quantidade:1},
    {tipo:'promocional',oferta_tipo:'promocional',preco:60,quantidade:1,tag:'PROMOÇÃO INDIVIDUAL',origem:'Promoção individual'},
    {tipo:'combo',oferta_tipo:'combo',preco:50,quantidade:3,promocao_id:1,faixa_id:2},
    {tipo:'kit',oferta_tipo:'kit',preco:40,total:40,quantidade:1},
  ]};
  assert.equal(offers.precoCatalogo(product),50,'Catalog shows the lowest comparable unit price');
  assert.equal(offers.ofertaCatalogo(product).quantidade,3,'Conditional minimum remains visible');
  offers.adicionarItem(product);
  assert.equal(offers.modalPreco.show,true);
  assert.equal(offers.venda.itens.length,0,'Wait for attendant confirmation before adding a condition');
  offers.confirmarPreco(product.ofertas[1]);
  assert.equal(offers.venda.itens[0].valor_unitario,60);
  assert.equal(offers.precoReferenciaItem(offers.venda.itens[0]),100);
  assert.equal(offers.venda.itens[0]._precoOriginal,60,'Manual price restore must keep the selected promotion');
  assert.equal(offers.campanhaDistinta('PROMOÇÃO INDIVIDUAL','Promoção individual'),false);
  assert.equal(offers.campanhaDistinta('COMBO','Saldão'),true);
  offers._pushItem(product,product.ofertas[2],true);
  const stockText=offers.resumoEstoqueCarrinho(offers.venda.itens[0]);
  assert.equal(stockText,'Em estoque: 5 · Restam após esta venda: 1','Sum separate offer lines without repeating quantity');
  offers.venda.itens[1].quantidade=6;
  assert.match(offers.resumoEstoqueCarrinho(offers.venda.itens[0]),/Estoque ficará negativo: −2/);
  assert.equal(offers.infoEstoqueCarrinho(offers.venda.itens[0]).negativo,true);
  assert.equal(product.estoque_disponivel,5,'Cart preview must not mutate real stock');
  offers.venda.itens.push({produto_id:10,quantidade:2,oferta_brindes_estoque:[{produto_id:9,quantidade:1}],brinde_quantidade_gatilho:2});
  assert.match(offers.resumoEstoqueCarrinho(offers.venda.itens[0]),/Estoque ficará negativo: −3/,'Include gifts using the same stock');
  offers.venda.itens.push({tipo_venda:'kit',quantidade:2,oferta_componentes_estoque:[{produto_id:9,quantidade:3}]});
  assert.match(offers.resumoEstoqueCarrinho(offers.venda.itens[0]),/Estoque ficará negativo: −9/);
  assert.match(offers.resumoEstoqueCarrinho({produto_id:99}),/não consultado/);
  assert.equal(offers.precoCatalogo({preco:12}),12);
  assert.equal(offers.precoCatalogo({preco:12,ofertas:[{tipo:'promocional',preco:0}]}),0);
  assert.equal(offers.percentualOferta({economia_percentual:20}), '20');
  assert.equal(offers.percentualOferta({economia_percentual:44.45}), '44,45');
  assert.equal(offers.validadeModalOferta({tipo:'promocional',validade:'A partir de 30/08/2026'}),'Promoção válida a partir de 30/08/2026');
  assert.equal(offers.validadeModalOferta({tipo:'promocional',validade:'30/08/2026 a 30/09/2026 · seg'}),'Promoção válida a partir de 30/08/2026 até 30/09/2026 · seg');
  assert.equal(offers.validadeModalOferta({tipo:'normal',validade:'Preço vigente do cadastro'}),'');
  assert.equal(offers.validadeModalOferta({tipo:'promocional',validade:'Sem data final'}),'Promoção sem prazo final');
  const priceEdit=create();
  priceEdit.$nextTick=fn=>fn(); priceEdit.$refs={};
  const priceItem={quantidade:2,valor_unitario:50,valor_total:90,desconto_valor:10,_precoOriginal:50,custo_atual:40};
  priceEdit.venda.itens=[priceItem];
  priceEdit.abrirPrecoItem(priceItem,true);
  assert.equal(priceEdit.editandoTotalItem,true);
  priceEdit.aplicarPrecoItem(priceItem,'50,00');
  assert.equal(priceItem.valor_unitario,30,'Manual price below cost is allowed');
  assert.equal(priceItem.valor_total,50,'Click edits line total, including quantity and current discount');
  assert.equal(priceItem.preco_manual,30);
  assert.equal(priceItem.desconto_valor,10);
  priceEdit.abrirPrecoItem(priceItem);
  assert.equal(priceEdit.editandoTotalItem,false,'Price button still edits unit price');
  priceEdit.aplicarPrecoItem(priceItem,'20');
  assert.equal(priceItem.valor_unitario,20);
  priceEdit.toast_=()=>{};
  priceEdit.aplicarPrecoItem(priceItem,'-1');
  assert.equal(priceItem.valor_unitario,20,'Negative price remains invalid');
  for (const invalid of ['', ' ', 'abc', 'NaN', 'Infinity', '-0,01']) {
    priceEdit.aplicarPrecoItem(priceItem,invalid);
    assert.equal(priceItem.valor_unitario,20,'Invalid input must not accidentally make an item free');
  }
  priceEdit.abrirPrecoItem(priceItem,true);
  priceEdit.aplicarPrecoItem(priceItem,'0,00');
  assert.equal(priceItem.valor_total,0);
  assert.equal(priceEdit.itemGratis(priceItem),true,'100% discounted total is free');
  priceEdit.abrirPrecoItem(priceItem);
  priceEdit.aplicarPrecoItem(priceItem,'0');
  assert.equal(priceItem.valor_unitario,0);
  assert.equal(priceItem.preco_manual,0);
  assert.equal(priceItem._precoManual,true);
  priceEdit.sessao={id:1};
  assert.equal(!!priceEdit.podeFinalizar,true,'Free sale does not need a payment');
  priceEdit.restaurarPrecoItem(priceItem);
  assert.equal(priceEdit.itemGratis(priceItem),false,'Tag disappears when price is restored');
  assert.equal(priceEdit.itemGratis({}),false);
  const stockApp=create();
  const stockItem={produto_id:1,quantidade:8,estoque_disponivel:8};
  stockApp.venda.itens=[stockItem];
  assert.equal(stockApp.infoEstoqueCarrinho(stockItem).negativo,false);
  stockItem.quantidade=9;
  assert.equal(stockApp.resumoEstoqueCarrinho(stockItem),'Em estoque: 8 · Estoque ficará negativo: −1');
  stockItem.quantidade=7;
  assert.equal(stockApp.infoEstoqueCarrinho(stockItem).negativo,false);
  const resumeFree=create();
  resumeFree.novaVenda=()=>{resumeFree.venda.itens=[];};
  resumeFree.carregarPendentes=async()=>{};
  resumeFree.buscarProdutos=async()=>{};
  resumeFree.carregarTopProdutos=async()=>{};
  context.csrf=()=>'';
  const resumeRequests=[];
  context.fetch=async url=>{
    resumeRequests.push(url);
    return {ok:true,json:async()=>({ok:true,itens:[{produto_id:1,quantidade:1,valor_unitario:0,valor_total:0,preco_manual:0,preco_original:10}]})};
  };
  await resumeFree.retomarPendente({id:1,numero_venda:1});
  assert.equal(resumeFree.venda.itens[0]._precoManual,true,'Numeric zero must remain manual after resuming');
  assert.equal(resumeFree.venda.itens[0].preco_manual,0);
  assert.equal(resumeFree.itemGratis(resumeFree.venda.itens[0]),true);
  assert.equal(resumeRequests.includes('/pdv/api/precos-cliente/'),false,'Do not reprice free items');

  const finalDiscount=create();
  finalDiscount.$nextTick=fn=>fn(); finalDiscount.$refs={};
  finalDiscount.venda.itens=[{quantidade:3,valor_total:209.97}];
  finalDiscount.recalcularTotais(); finalDiscount.abrirDescontoGeral();
  finalDiscount.sincronizarTotalFinal('200,00');
  assert.equal(finalDiscount.descontoGeralRascunho.valor,'9,97');
  assert.equal(finalDiscount.descontoGeralRascunho.percentual,'4,75');
  assert.equal(finalDiscount.venda.total,209.97,'Do not mutate sale while editing final total');
  finalDiscount.aplicarDescontoGeralValor();
  assert.equal(finalDiscount.venda.total,200);
  finalDiscount.sincronizarDesconto(finalDiscount.descontoGeralRascunho,'valor','10',209.97);
  assert.equal(finalDiscount.descontoGeralRascunho.totalFinal,'199,97');
  finalDiscount.venda.acrescimo=5;
  finalDiscount.sincronizarTotalFinal('200,');
  assert.equal(finalDiscount.descontoGeralRascunho.totalFinal,'200,');
  assert.equal(finalDiscount.descontoGeralRascunho.valor,'14,97');
  finalDiscount.sincronizarTotalFinal('-10');
  assert.equal(finalDiscount.descontoGeralRascunho.totalFinal,'5,00','Do not discount a surcharge');
  finalDiscount.sincronizarTotalFinal('900');
  assert.equal(finalDiscount.descontoGeralRascunho.totalFinal,'214,97');
  finalDiscount.venda.subtotal=0; finalDiscount.venda.acrescimo=0;
  finalDiscount.sincronizarTotalFinal('10');
  assert.equal(finalDiscount.descontoGeralRascunho.percentual,'0,00');
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
  context.csrf=()=> 'test-token';
  respond({ok:true,url:'https://ited.app.br/comprovante/token-secreto/'});
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(nativeShare,0,'Desktop must not open the Windows share dialog');
  assert.equal(downloaded,0,'Link sharing must not download an attachment');
  assert.equal(opened,1);
  const wa=new URL(url);
  assert.equal(wa.origin,'https://web.whatsapp.com');
  assert.equal(wa.searchParams.get('phone'),'5555999991234');
  assert.match(wa.searchParams.get('text'),/100,00/);
  assert.match(wa.searchParams.get('text'),/https:\/\/ited.app.br\/comprovante\/token-secreto\//);
  context.navigator.userAgent='iPhone';
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(new URL(url).origin,'https://wa.me');
  assert.equal(nativeShare,0);
  assert.equal(downloaded,0);
  context.navigator.userAgent='Windows';
  context.window.prompt=()=>null;
  await share.compartilharCupomWhatsApp({id:1});
  assert.equal(opened,2,'Cancel must not download or open a tab');
  assert.equal(downloaded,0);
  context.window.prompt=()=>'';
  respond({erro:'Venda indisponível'},false);
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
