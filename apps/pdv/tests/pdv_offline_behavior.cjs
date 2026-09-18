const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

const context = vm.createContext({
  Date,
  URLSearchParams,
  navigator: {onLine: false},
  window: {location: {search: ''}, addEventListener: () => {}},
  setInterval: () => 1,
  clearTimeout: () => {},
  setTimeout: () => 1,
});
vm.runInContext(fs.readFileSync(0, 'utf8'), context);

const app = context.pdv();
app._localStore = {};
app.sessao = {id: 10};
app.modo = 'venda';
app.conectividadeOnline = true;
assert.equal(app.statusConexaoLabel, 'ONLINE');
app.conectividadeOnline = false;
assert.equal(app.statusConexaoLabel, 'SEM INTERNET');
app.sincronizandoFilaOffline = true;
assert.equal(app.statusConexaoLabel, 'SINCRONIZANDO');
app.sincronizandoFilaOffline = false;
app.snapshotLocal = {
  catalogo_em: new Date().toISOString(),
  produtos: [
    {id: 1, descricao: 'Açúcar Cristal', codigo_barras: '789100000001', linha_id: 4},
    {id: 2, descricao: 'Café Torrado', codigo_barras: '789100000002', linha_id: 5},
  ],
};
app.formasPagamento = [
  {id: 1, tipo: 'dinheiro', requer_tef: false},
  {id: 2, tipo: 'cartao_credito', requer_tef: true},
];

const seguro = app.produtoLocalSeguro({
  id: 8,
  preco: 70,
  preco_base: 100,
  custo_atual: 40,
  margem_percentual: 60,
  ofertas: [{oferta_tipo: 'promocional', preco: 70}],
});
assert.equal(seguro.preco, 100);
assert.equal(seguro.ofertas.length, 1);
assert.equal(seguro.ofertas[0].oferta_tipo, 'normal');
assert.equal(Object.hasOwn(seguro, 'custo_atual'), false);
assert.equal(Object.hasOwn(seguro, 'margem_percentual'), false);

assert.equal(app.buscarProdutosNoSnapshot('acucar').produtos[0].id, 1);
assert.equal(app.buscarProdutosNoSnapshot('789100000002').produtos[0].id, 2);
app.linhaAtiva = 4;
assert.deepEqual(Array.from(app.buscarProdutosNoSnapshot('a').produtos, item => item.id), [1]);
app.linhaAtiva = null;

const payload = {
  cliente_id: null,
  pagamentos: [{forma_id: 1, forma_tipo: 'dinheiro'}],
  credito_valor: 0,
  delivery: false,
  venda_fora_estabelecimento: false,
  comanda_id: null,
  venda_edicao_origem_id: null,
};
assert.equal(app.motivoBloqueioFilaOffline(payload), '');
assert.match(app.motivoBloqueioFilaOffline({...payload, cliente_id: 99}), /Consumidor Final/);
assert.match(app.motivoBloqueioFilaOffline({...payload, pagamentos: [{forma_id: 2}]}), /validação online/);
app.snapshotLocal.catalogo_em = new Date(Date.now() - 13 * 60 * 60 * 1000).toISOString();
assert.match(app.motivoBloqueioFilaOffline(payload), /mais de 12 horas/);

const vendaSegura = app.vendaLocalSegura({
  itens: [{produto_id: 1, custo_atual: 10, margem_percentual: 50, valor_unitario: 20}],
});
assert.equal(Object.hasOwn(vendaSegura.itens[0], 'custo_atual'), false);
assert.equal(Object.hasOwn(vendaSegura.itens[0], 'margem_percentual'), false);
assert.equal(vendaSegura.itens[0].valor_unitario, 20);
