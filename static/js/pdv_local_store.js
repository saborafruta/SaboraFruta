(function (global) {
  'use strict';

  const DB_NAME = 'saborafruta-pdv-local';
  const DB_VERSION = 3;
  const DRAFTS_STORE = 'rascunhos';
  const META_STORE = 'metadados';
  const SNAPSHOTS_STORE = 'snapshots';
  const QUEUE_STORE = 'vendas_pendentes';
  const OFFLINE_PROFILES_STORE = 'perfis_offline';
  const OFFLINE_AUTH_HOURS = 12;

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

  function bytesToBase64(bytes) {
    let binary = '';
    bytes.forEach(value => { binary += String.fromCharCode(value); });
    return global.btoa(binary);
  }

  function base64ToBytes(value) {
    const binary = global.atob(value);
    return Uint8Array.from(binary, char => char.charCodeAt(0));
  }

  async function derivePinKey(pin, salt) {
    if (!global.crypto?.subtle) throw new Error('Criptografia local indisponivel neste navegador.');
    const material = await global.crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(String(pin)),
      'PBKDF2',
      false,
      ['deriveKey'],
    );
    return global.crypto.subtle.deriveKey(
      {name: 'PBKDF2', salt, iterations: 210000, hash: 'SHA-256'},
      material,
      {name: 'AES-GCM', length: 256},
      false,
      ['encrypt', 'decrypt'],
    );
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
          if (!db.objectStoreNames.contains(OFFLINE_PROFILES_STORE)) {
            db.createObjectStore(OFFLINE_PROFILES_STORE, { keyPath: 'scope' });
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

    async configureOfflineAccess(pin, profile) {
      if (!/^\d{6}$/.test(String(pin || ''))) {
        throw new Error('Crie um PIN local com exatamente 6 numeros.');
      }
      const salt = global.crypto.getRandomValues(new Uint8Array(16));
      const iv = global.crypto.getRandomValues(new Uint8Array(12));
      const key = await derivePinKey(pin, salt);
      const proof = new TextEncoder().encode(JSON.stringify({
        scope: this.scope,
        installation_id: this.installationId,
        marker: generateId('unlock'),
      }));
      const encrypted = new Uint8Array(await global.crypto.subtle.encrypt({name: 'AES-GCM', iv}, key, proof));
      const now = new Date();
      const record = {
        scope: this.scope,
        filial_id: this.filialId,
        usuario_id: this.usuarioId,
        installation_id: this.installationId,
        filial_nome: String(profile?.filial_nome || 'Filial'),
        usuario_nome: String(profile?.usuario_nome || 'Operador'),
        usuario_login: String(profile?.usuario_login || ''),
        salt: bytesToBase64(salt),
        iv: bytesToBase64(iv),
        proof: bytesToBase64(encrypted),
        enabled_at: now.toISOString(),
        last_online_at: now.toISOString(),
        valid_until: new Date(now.getTime() + OFFLINE_AUTH_HOURS * 60 * 60 * 1000).toISOString(),
        failed_attempts: 0,
        blocked_until: null,
      };
      await this._put(OFFLINE_PROFILES_STORE, record, true);
      return clonePlain(record);
    }

    async getOfflineProfile() {
      const profile = await this._get(OFFLINE_PROFILES_STORE, this.scope);
      return profile ? clonePlain(profile) : null;
    }

    async refreshOfflineAccess(profile) {
      const current = await this._get(OFFLINE_PROFILES_STORE, this.scope);
      if (!current || current.installation_id !== this.installationId) return null;
      const now = new Date();
      const record = {
        ...current,
        filial_nome: String(profile?.filial_nome || current.filial_nome || 'Filial'),
        usuario_nome: String(profile?.usuario_nome || current.usuario_nome || 'Operador'),
        usuario_login: String(profile?.usuario_login || current.usuario_login || ''),
        last_online_at: now.toISOString(),
        valid_until: new Date(now.getTime() + OFFLINE_AUTH_HOURS * 60 * 60 * 1000).toISOString(),
        failed_attempts: 0,
        blocked_until: null,
      };
      await this._put(OFFLINE_PROFILES_STORE, record, true);
      return clonePlain(record);
    }

    async disableOfflineAccess() {
      await this._delete(OFFLINE_PROFILES_STORE, this.scope, true);
    }

    static async listOfflineProfiles() {
      const db = await PDVLocalStore._openDatabase();
      const profiles = await new Promise((resolve, reject) => {
        const request = db.transaction(OFFLINE_PROFILES_STORE, 'readonly')
          .objectStore(OFFLINE_PROFILES_STORE).getAll();
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error || new Error('Falha ao listar acessos offline.'));
      });
      db.close();
      return profiles.map(clonePlain).sort((a, b) => String(b.last_online_at).localeCompare(String(a.last_online_at)));
    }

    static async unlockOfflineProfile(scope, pin) {
      const db = await PDVLocalStore._openDatabase();
      const read = key => new Promise((resolve, reject) => {
        const request = db.transaction(OFFLINE_PROFILES_STORE, 'readonly')
          .objectStore(OFFLINE_PROFILES_STORE).get(key);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error || new Error('Falha ao ler acesso offline.'));
      });
      const write = value => new Promise((resolve, reject) => {
        let transaction;
        try { transaction = db.transaction(OFFLINE_PROFILES_STORE, 'readwrite', {durability: 'strict'}); }
        catch (_) { transaction = db.transaction(OFFLINE_PROFILES_STORE, 'readwrite'); }
        transaction.objectStore(OFFLINE_PROFILES_STORE).put(value);
        transaction.oncomplete = resolve;
        transaction.onabort = transaction.onerror = () => reject(transaction.error || new Error('Falha ao atualizar acesso offline.'));
      });
      const profile = await read(scope);
      if (!profile) { db.close(); throw new Error('Acesso offline nao autorizado neste computador.'); }
      const now = Date.now();
      if (profile.blocked_until && new Date(profile.blocked_until).getTime() > now) {
        db.close();
        throw new Error('PIN temporariamente bloqueado. Aguarde alguns minutos.');
      }
      if (!profile.valid_until || new Date(profile.valid_until).getTime() < now) {
        db.close();
        throw new Error('Autorizacao offline vencida. Conecte o PDV para renovar.');
      }
      try {
        const key = await derivePinKey(pin, base64ToBytes(profile.salt));
        const clear = await global.crypto.subtle.decrypt(
          {name: 'AES-GCM', iv: base64ToBytes(profile.iv)},
          key,
          base64ToBytes(profile.proof),
        );
        const proof = JSON.parse(new TextDecoder().decode(clear));
        if (proof.scope !== scope || proof.installation_id !== profile.installation_id) throw new Error('invalid proof');
        profile.failed_attempts = 0;
        profile.blocked_until = null;
        await write(profile);
        db.close();
        return clonePlain(profile);
      } catch (_) {
        profile.failed_attempts = Number(profile.failed_attempts || 0) + 1;
        if (profile.failed_attempts >= 5) {
          profile.blocked_until = new Date(now + 5 * 60 * 1000).toISOString();
          profile.failed_attempts = 0;
        }
        await write(profile);
        db.close();
        throw new Error('PIN local incorreto.');
      }
    }

    static async _openDatabase() {
      if (!global.indexedDB) throw new Error('IndexedDB indisponivel neste navegador.');
      return new Promise((resolve, reject) => {
        const request = global.indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = () => {
          const db = request.result;
          if (!db.objectStoreNames.contains(DRAFTS_STORE)) db.createObjectStore(DRAFTS_STORE, {keyPath: 'scope'});
          if (!db.objectStoreNames.contains(META_STORE)) db.createObjectStore(META_STORE, {keyPath: 'key'});
          if (!db.objectStoreNames.contains(SNAPSHOTS_STORE)) db.createObjectStore(SNAPSHOTS_STORE, {keyPath: 'scope'});
          if (!db.objectStoreNames.contains(QUEUE_STORE)) {
            const queue = db.createObjectStore(QUEUE_STORE, {keyPath: 'local_id'});
            queue.createIndex('scope', 'scope', {unique: false});
          }
          if (!db.objectStoreNames.contains(OFFLINE_PROFILES_STORE)) db.createObjectStore(OFFLINE_PROFILES_STORE, {keyPath: 'scope'});
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('Falha ao abrir armazenamento local.'));
      });
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
