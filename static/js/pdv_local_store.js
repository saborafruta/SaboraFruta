(function (global) {
  'use strict';

  const DB_NAME = 'saborafruta-pdv-local';
  const DB_VERSION = 2;
  const DRAFTS_STORE = 'rascunhos';
  const META_STORE = 'metadados';
  const SNAPSHOTS_STORE = 'snapshots';
  const QUEUE_STORE = 'vendas_pendentes';

  function clonePlain(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function randomHex(bytes) {
    const values = new Uint8Array(bytes);
    if (global.crypto && global.crypto.getRandomValues) {
      global.crypto.getRandomValues(values);
    } else {
      for (let index = 0; index < values.length; index += 1) {
        values[index] = Math.floor(Math.random() * 256);
      }
    }
    return Array.from(values, value => value.toString(16).padStart(2, '0')).join('');
  }

  function generateId(prefix) {
    const uuid = global.crypto && typeof global.crypto.randomUUID === 'function'
      ? global.crypto.randomUUID().replace(/-/g, '')
      : randomHex(16);
    return `${prefix || 'pdv'}${uuid}`;
  }

  class PDVLocalStore {
    constructor(options) {
      this.filialId = String(options.filialId);
      this.usuarioId = String(options.usuarioId);
      this.scope = `${this.filialId}:${this.usuarioId}`;
      this.db = null;
      this.installationId = null;
    }

    async init() {
      if (!global.indexedDB) throw new Error('IndexedDB indisponivel neste navegador.');
      this.db = await new Promise((resolve, reject) => {
        const request = global.indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(DRAFTS_STORE)) {
            db.createObjectStore(DRAFTS_STORE, { keyPath: 'scope' });
          }
          if (!db.objectStoreNames.contains(META_STORE)) {
            db.createObjectStore(META_STORE, { keyPath: 'key' });
          }
          if (!db.objectStoreNames.contains(SNAPSHOTS_STORE)) {
            db.createObjectStore(SNAPSHOTS_STORE, { keyPath: 'scope' });
          }
          if (!db.objectStoreNames.contains(QUEUE_STORE)) {
            const queue = db.createObjectStore(QUEUE_STORE, { keyPath: 'local_id' });
            queue.createIndex('scope', 'scope', { unique: false });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Falha ao abrir armazenamento local.'));
        request.onblocked = () => reject(new Error('Atualizacao do armazenamento local bloqueada.'));
      });
      this.db.onversionchange = () => this.db.close();
      this.installationId = await this._getMeta('installation_id');
      if (!this.installationId) {
        this.installationId = generateId('inst');
        await this._put(META_STORE, { key: 'installation_id', value: this.installationId });
      }
      if (global.navigator && global.navigator.storage && global.navigator.storage.persist) {
        try { await global.navigator.storage.persist(); } catch (_) { /* best effort */ }
      }
      return this;
    }

    newSaleId() {
      return generateId('pdv');
    }

    async loadDraft() {
      const draft = await this._get(DRAFTS_STORE, this.scope);
      return draft ? clonePlain(draft) : null;
    }

    async saveDraft(data) {
      const record = clonePlain({
        ...data,
        scope: this.scope,
        filial_id: this.filialId,
        usuario_id: this.usuarioId,
        installation_id: this.installationId,
        updated_at: new Date().toISOString(),
      });
      await this._put(DRAFTS_STORE, record, true);
      return record;
    }

    async deleteDraft() {
      await this._delete(DRAFTS_STORE, this.scope, true);
    }

    async saveSnapshot(data) {
      const record = clonePlain({
        ...data,
        scope: this.scope,
        filial_id: this.filialId,
        usuario_id: this.usuarioId,
        installation_id: this.installationId,
        updated_at: new Date().toISOString(),
      });
      await this._put(SNAPSHOTS_STORE, record, true);
      return record;
    }

    async loadSnapshot() {
      const snapshot = await this._get(SNAPSHOTS_STORE, this.scope);
      return snapshot ? clonePlain(snapshot) : null;
    }

    async enqueueSale(data) {
      if (!data || !data.local_id) throw new Error('Venda local sem identificador.');
      const record = clonePlain({
        ...data,
        scope: this.scope,
        filial_id: this.filialId,
        usuario_id: this.usuarioId,
        installation_id: this.installationId,
        status: data.status || 'pendente',
        attempts: Number(data.attempts || 0),
        created_at: data.created_at || new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
      await this._put(QUEUE_STORE, record, true);
      return record;
    }

    async listQueuedSales() {
      const records = await this._getAllByIndex(QUEUE_STORE, 'scope', this.scope);
      return records.map(clonePlain).sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
    }

    async updateQueuedSale(localId, changes) {
      const current = await this._get(QUEUE_STORE, localId);
      if (!current || current.scope !== this.scope) return null;
      return this.enqueueSale({ ...current, ...changes, local_id: localId });
    }

    async deleteQueuedSale(localId) {
      const current = await this._get(QUEUE_STORE, localId);
      if (current?.scope === this.scope) await this._delete(QUEUE_STORE, localId, true);
    }

    async _getMeta(key) {
      const result = await this._get(META_STORE, key);
      return result ? result.value : null;
    }

    _transaction(store, mode, strict) {
      if (strict) {
        try { return this.db.transaction(store, mode, { durability: 'strict' }); } catch (_) { /* compat */ }
      }
      return this.db.transaction(store, mode);
    }

    async _get(store, key) {
      return new Promise((resolve, reject) => {
        const request = this._transaction(store, 'readonly', false).objectStore(store).get(key);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error || new Error('Falha ao ler armazenamento local.'));
      });
    }

    async _getAllByIndex(store, index, key) {
      return new Promise((resolve, reject) => {
        const request = this._transaction(store, 'readonly', false)
          .objectStore(store).index(index).getAll(key);
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error || new Error('Falha ao listar armazenamento local.'));
      });
    }

    async _put(store, value, strict) {
      return new Promise((resolve, reject) => {
        const transaction = this._transaction(store, 'readwrite', strict);
        transaction.objectStore(store).put(value);
        transaction.oncomplete = () => resolve();
        transaction.onabort = transaction.onerror = () => reject(
          transaction.error || new Error('Falha ao gravar armazenamento local.'),
        );
      });
    }

    async _delete(store, key, strict) {
      return new Promise((resolve, reject) => {
        const transaction = this._transaction(store, 'readwrite', strict);
        transaction.objectStore(store).delete(key);
        transaction.oncomplete = () => resolve();
        transaction.onabort = transaction.onerror = () => reject(
          transaction.error || new Error('Falha ao limpar armazenamento local.'),
        );
      });
    }
  }

  PDVLocalStore.generateId = generateId;
  global.PDVLocalStore = PDVLocalStore;
})(window);
