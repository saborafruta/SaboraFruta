const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../../../../static/js/op2_clientes.js'), 'utf8');
const principal = {id: 1, nome: 'Principal', contato: 'Contato', telefone: '999'};
const adicional = {id: 2, nome: 'Adicional'};
const ok = dados => ({ok: true, status: 200, headers: new Headers({'content-type': 'application/json'}), json: async () => dados});

function setup(fetch = async () => ok({clientes: []})) {
  const timers = new Map(); let id = 0;
  const context = vm.createContext({fetch, FormData: class {}, setTimeout(fn) {timers.set(++id, fn); return id;}, clearTimeout(id) {timers.delete(id);}});
  vm.runInContext(source, context);
  const campos = {razao_social: {value: ''}, contribuinte_icms: {type: 'checkbox', checked: false}};
  const raw = context.configurarClientesOp2({clientes: [], clientesAdicionais: [], clienteId: '', buscaCliente: '', clienteResultados: [], clienteBuscaSeq: 0, adicionandoCliente: false}, {buscar: '/buscar/', criar: '/criar/', editar: '/clientes/0/editar/'});
  const writes = [];
  const state = new Proxy(raw, {set(target, key, value) {writes.push(key); return Reflect.set(target, key, value);}});
  state.$refs = {buscaCliente: {focus() {}}, novo: {reset() {}, elements: {namedItem: name => campos[name]}}};
  state.$nextTick = async fn => {if (fn) fn();};
  return {state, writes, timers, campos};
}

test('selecionar atualiza os campos pelo proxy reativo', () => {
  const {state, writes} = setup();
  state.selecionarCliente(principal);
  for (const campo of ['clienteId', 'buscaCliente', 'contatoNome', 'contatoTelefone']) assert.ok(writes.includes(campo));
  assert.equal(state.clienteId, '1'); assert.equal(state.contatoNome, 'Contato');
});

test('seleção cancela busca agendada e resposta atrasada não reabre resultados', async () => {
  let responder;
  const {state, timers} = setup(() => new Promise(resolve => responder = resolve));
  state.buscaCliente = 'Principal'; state.digitarCliente();
  assert.equal(timers.size, 1);
  const busca = state.buscarClientes();
  state.selecionarCliente(principal);
  assert.equal(timers.size, 0);
  responder(ok({clientes: [adicional]})); await busca;
  assert.equal(state.clienteResultados.length, 0); assert.equal(state.clienteId, '1');
});

test('adicionar sem principal permite pesquisar; adicional não substitui principal', () => {
  const {state} = setup(); state.adicionarCliente();
  assert.equal(state.adicionandoCliente, false);
  state.selecionarCliente(principal); state.adicionarCliente(); state.selecionarCliente(adicional);
  assert.equal(state.clienteId, '1'); assert.equal(state.buscaCliente, 'Principal');
  assert.equal(state.clientesAdicionais[0].id, 2);
});

test('resultado já filtrado pelo servidor não é perdido por formatação do documento', () => {
  const {state} = setup(); state.buscaCliente = '12345678901';
  state.clienteResultados = [{id: 1, nome: 'Pessoa', documento: '123.456.789-01'}];
  assert.equal(state.clientesVisiveis().length, 1);
});

test('cadastro rápido seleciona o novo cliente mantendo os itens da OP', async () => {
  const {state} = setup(async () => ok({ok: true, cliente: principal}));
  state.itens = [{nome: 'Modelo'}]; state.modalCliente = true;
  await state.salvarCliente();
  assert.equal(state.clienteId, '1'); assert.equal(state.modalCliente, false);
  assert.equal(state.itens.length, 1);
});

test('edição carrega o id selecionado e atualiza os contatos sem navegar', async () => {
  const requests = [];
  const {state, campos} = setup(async (url, options) => {
    requests.push(url);
    return ok(options.method ? {ok: true, cliente: {...principal, contato: 'Novo'}} : {ok: true, campos: {razao_social: 'Original', contribuinte_icms: true}});
  });
  state.selecionarCliente(principal);
  await state.abrirCadastroCliente(state.clienteId);
  assert.equal(campos.razao_social.value, 'Original'); assert.equal(campos.contribuinte_icms.checked, true);
  await state.salvarCliente();
  assert.deepEqual(requests, ['/clientes/1/editar/', '/clientes/1/editar/']);
  assert.equal(state.contatoNome, 'Novo');
});

test('editar adicional mantém o cliente principal e atualiza a tag', async () => {
  const {state} = setup(async () => ok({ok: true, cliente: {...adicional, nome: 'Corrigido'}}));
  state.selecionarCliente(principal); state.clientesAdicionais = [adicional]; state.clienteEditandoId = '2';
  await state.salvarCliente();
  assert.equal(state.clienteId, '1'); assert.equal(state.clientesAdicionais[0].nome, 'Corrigido');
});

test('erros de permissão e de formulário aparecem sem fechar o cadastro', async () => {
  const {state} = setup(async () => ({status: 403}));
  state.buscaCliente = 'Cliente'; await state.buscarClientes();
  assert.match(state.clienteBuscaErro, /permissão/);
  const invalid = setup(async () => ({...ok({ok: false, erros: {razao_social: ['Nome inválido']}}), ok: false, status: 400})).state;
  invalid.modalCliente = true; await invalid.salvarCliente();
  assert.equal(invalid.erros.razao_social, 'Nome inválido'); assert.equal(invalid.modalCliente, true);
});

test('falha ao carregar edição bloqueia salvar dados vazios', async () => {
  const {state} = setup(async () => ({status: 403}));
  await state.abrirCadastroCliente('1');
  assert.equal(state.clienteCarregando, true); assert.match(state.erro, /permissão/);
});
