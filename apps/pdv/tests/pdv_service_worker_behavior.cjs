const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const handlers = {};
const shellResponse = {kind: 'pdv-offline-shell'};
const context = vm.createContext({
  URL,
  Response,
  Promise,
  fetch: async () => { throw new Error('offline'); },
  caches: {
    match: async key => String(key).includes('pdv-offline.html') ? shellResponse : null,
    open: async () => ({add: async () => {}, put: async () => {}}),
    keys: async () => [],
    delete: async () => true,
  },
  self: {
    addEventListener: (name, handler) => { handlers[name] = handler; },
    skipWaiting: async () => {},
    clients: {claim: async () => {}},
  },
});

vm.runInContext(fs.readFileSync(0, 'utf8'), context);

async function navigate(path) {
  let responsePromise;
  handlers.fetch({
    request: {method: 'GET', mode: 'navigate', url: `https://erp.example${path}`},
    respondWith: promise => { responsePromise = promise; },
  });
  return responsePromise;
}

(async () => {
  assert.equal(await navigate('/pdv/'), shellResponse);
  assert.equal(await navigate('/pdv/?caixa=1'), shellResponse);
  const generic = await navigate('/financeiro/');
  assert.equal(generic.status, 503);
  assert.match(await generic.text(), /Sem conex/);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
