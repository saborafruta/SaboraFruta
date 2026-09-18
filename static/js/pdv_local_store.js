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

  function generateRecoveryCode() {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    const values = new Uint8Array(16);
    global.crypto.getRandomValues(values);
    const raw = Array.from(values, value => alphabet[value % alphabet.length]).join('');
    return raw.match(/.{1,4}/g).join('-');
  }

  function normalizeRecoveryCode(value) {
    return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
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

  async function deriveBackupKey(password, salt) {
    if (!global.crypto?.subtle) throw new Error('Criptografia local indisponivel neste navegador.');
    const material = await global.crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(String(password)),
      'PBKDF2',
      false,
      ['deriveKey'],
    );
    return global.crypto.subtle.deriveKey(
      {name: 'PBKDF2', salt, iterations: 310000, hash: 'SHA-256'},
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

    async operationalSummary() {
      const queue = await this.listQueuedSales();
      const snapshot = await this.loadSnapshot();
      const errors = queue.filter(item => item.status === 'erro');
      const latestError = queue.slice().reverse().find(item => item.last_error);
      return {
        fila_pendente_quantidade: queue.length,
        fila_erro_quantidade: errors.length,
        fila_resumo: queue.slice(0, 1000).map(item => ({
          local_id: item.local_id,
          status: item.status === 'erro' ? 'erro' : 'pendente',
          attempts: Number(item.attempts || 0),
          created_at: item.created_at || null,
          last_error: String(item.last_error || '').slice(0, 300),
        })),
        catalogo_atualizado_em: snapshot?.catalogo_em || snapshot?.gerado_em || null,
        ultimo_erro_sincronizacao: String(latestError?.last_error || '').slice(0, 1000),
        ultima_sincronizacao_em: await this._getMeta('ultima_sincronizacao_em'),
        ultimo_backup_em: await this._getMeta('ultimo_backup_em'),
      };
    }

    async markQueueSynchronized() {
      const value = new Date().toISOString();
      await this._put(META_STORE, {key: 'ultima_sincronizacao_em', value}, true);
      return value;
    }

    async exportEmergencyBackup(password) {
      if (String(password || '').length < 8) {
        throw new Error('Crie uma senha de backup com pelo menos 8 caracteres.');
      }
      const queue = await this.listQueuedSales();
      const draft = await this.loadDraft();
      if (!queue.length && !draft) throw new Error('Nao ha vendas ou carrinho local para exportar.');
      const payload = {
        format: 'ited-pdv-emergency-backup',
        version: 1,
        created_at: new Date().toISOString(),
        scope: this.scope,
        filial_id: this.filialId,
        usuario_id: this.usuarioId,
        source_installation_id: this.installationId,
        queued_sales: queue,
        draft,
      };
      const salt = global.crypto.getRandomValues(new Uint8Array(16));
      const iv = global.crypto.getRandomValues(new Uint8Array(12));
      const key = await deriveBackupKey(password, salt);
      const aad = new TextEncoder().encode('ited-pdv-emergency-backup:v1');
      const encrypted = new Uint8Array(await global.crypto.subtle.encrypt(
        {name: 'AES-GCM', iv, additionalData: aad},
        key,
        new TextEncoder().encode(JSON.stringify(payload)),
      ));
      const exportedAt = new Date().toISOString();
      await this._put(META_STORE, {key: 'ultimo_backup_em', value: exportedAt}, true);
      return JSON.stringify({
        format: 'ited-pdv-emergency-backup',
        version: 1,
        kdf: {name: 'PBKDF2', hash: 'SHA-256', iterations: 310000},
        cipher: 'AES-GCM-256',
        salt: bytesToBase64(salt),
        iv: bytesToBase64(iv),
        ciphertext: bytesToBase64(encrypted),
      });
    }

    async importEmergencyBackup(fileText, password) {
      if (String(password || '').length < 8) throw new Error('Informe a senha usada ao exportar o backup.');
      if (String(fileText || '').length > 15 * 1024 * 1024) throw new Error('Arquivo de backup maior que o limite de 15 MB.');
      let envelope;
      try { envelope = JSON.parse(String(fileText || '')); }
      catch (_) { throw new Error('Arquivo de backup invalido.'); }
      if (envelope?.format !== 'ited-pdv-emergency-backup' || Number(envelope?.version) !== 1) {
        throw new Error('Formato de backup nao reconhecido.');
      }
      let payload;
      try {
        const salt = base64ToBytes(envelope.salt);
        const iv = base64ToBytes(envelope.iv);
        const key = await deriveBackupKey(password, salt);
        const clear = await global.crypto.subtle.decrypt(
          {name: 'AES-GCM', iv, additionalData: new TextEncoder().encode('ited-pdv-emergency-backup:v1')},
          key,
          base64ToBytes(envelope.ciphertext),
        );
        payload = JSON.parse(new TextDecoder().decode(clear));
      } catch (_) {
        throw new Error('Senha incorreta ou arquivo de backup corrompido.');
      }
      if (payload?.format !== 'ited-pdv-emergency-backup' || Number(payload?.version) !== 1) {
        throw new Error('Conteudo do backup invalido.');
      }
      if (String(payload.filial_id) !== this.filialId || String(payload.usuario_id) !== this.usuarioId || payload.scope !== this.scope) {
        throw new Error('Este backup pertence a outro usuario ou filial.');
      }
      const imported = {queued: 0, skipped: 0, draft: false};
      const queuedSales = Array.isArray(payload.queued_sales) ? payload.queued_sales.slice(0, 1000) : [];
      for (const source of queuedSales) {
        const localId = String(source?.local_id || '');
        if (!/^pdv[a-f0-9]{32}$/.test(localId)) { imported.skipped += 1; continue; }
        if (await this._get(QUEUE_STORE, localId)) { imported.skipped += 1; continue; }
        await this.enqueueSale({
          ...clonePlain(source),
          local_id: localId,
          endpoint: '/pdv/api/venda/finalizar/',
          status: source.status === 'erro' ? 'erro' : 'pendente',
          imported_at: new Date().toISOString(),
          source_installation_id: String(payload.source_installation_id || ''),
        });
        imported.queued += 1;
      }
      const currentDraft = await this.loadDraft();
      if (!currentDraft && payload.draft?.venda) {
        const draft = clonePlain(payload.draft);
        delete draft.scope;
        delete draft.filial_id;
        delete draft.usuario_id;
        delete draft.installation_id;
        await this.saveDraft({...draft, imported_at: new Date().toISOString()});
        imported.draft = true;
      }
      await this._put(META_STORE, {key: 'ultimo_backup_importado_em', value: new Date().toISOString()}, true);
      return imported;
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
      const recoveryCode = generateRecoveryCode();
      const recoverySalt = global.crypto.getRandomValues(new Uint8Array(16));
      const recoveryIv = global.crypto.getRandomValues(new Uint8Array(12));
      const recoveryKey = await derivePinKey(normalizeRecoveryCode(recoveryCode), recoverySalt);
      const recoveryProof = new TextEncoder().encode(JSON.stringify({
        scope: this.scope,
        installation_id: this.installationId,
        marker: generateId('recovery'),
      }));
      const recoveryEncrypted = new Uint8Array(await global.crypto.subtle.encrypt(
        {name: 'AES-GCM', iv: recoveryIv}, recoveryKey, recoveryProof,
      ));
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
        recovery_salt: bytesToBase64(recoverySalt),
        recovery_iv: bytesToBase64(recoveryIv),
        recovery_proof: bytesToBase64(recoveryEncrypted),
        recovery_generation: Number(profile?.recovery_generation || 1),
        recovery_failed_attempts: 0,
        recovery_blocked_until: null,
        server_revision: Number(profile?.server_revision || 0),
        nome_dispositivo: String(profile?.nome_dispositivo || ''),
        enabled_at: now.toISOString(),
        last_online_at: now.toISOString(),
        valid_until: new Date(now.getTime() + OFFLINE_AUTH_HOURS * 60 * 60 * 1000).toISOString(),
        failed_attempts: 0,
        blocked_until: null,
      };
      await this._put(OFFLINE_PROFILES_STORE, record, true);
      return {...clonePlain(record), recovery_code: recoveryCode};
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
        server_revision: Number(profile?.server_revision || current.server_revision || 0),
        nome_dispositivo: String(profile?.nome_dispositivo || current.nome_dispositivo || ''),
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

    static async recoverOfflineProfile(scope, recoveryCode, newPin) {
      if (!/^\d{6}$/.test(String(newPin || ''))) {
        throw new Error('Crie um novo PIN com exatamente 6 numeros.');
      }
      const normalizedCode = normalizeRecoveryCode(recoveryCode);
      if (normalizedCode.length !== 16) throw new Error('Codigo de emergencia invalido.');
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
      if (!profile?.recovery_proof) { db.close(); throw new Error('Este perfil nao possui codigo de emergencia. Conecte o PDV para reconfigurar.'); }
      const now = Date.now();
      if (!profile.valid_until || new Date(profile.valid_until).getTime() < now) {
        db.close();
        throw new Error('Autorizacao offline vencida. O codigo nao renova o prazo; conecte o PDV.');
      }
      if (profile.recovery_blocked_until && new Date(profile.recovery_blocked_until).getTime() > now) {
        db.close();
        throw new Error('Recuperacao temporariamente bloqueada. Aguarde 15 minutos.');
      }
      let recoveryVerified = false;
      try {
        const recoveryKey = await derivePinKey(normalizedCode, base64ToBytes(profile.recovery_salt));
        const clear = await global.crypto.subtle.decrypt(
          {name: 'AES-GCM', iv: base64ToBytes(profile.recovery_iv)},
          recoveryKey,
          base64ToBytes(profile.recovery_proof),
        );
        const recoveryProof = JSON.parse(new TextDecoder().decode(clear));
        if (recoveryProof.scope !== scope || recoveryProof.installation_id !== profile.installation_id) throw new Error('invalid proof');
        recoveryVerified = true;

        const pinSalt = global.crypto.getRandomValues(new Uint8Array(16));
        const pinIv = global.crypto.getRandomValues(new Uint8Array(12));
        const pinKey = await derivePinKey(newPin, pinSalt);
        const pinProof = new TextEncoder().encode(JSON.stringify({
          scope,
          installation_id: profile.installation_id,
          marker: generateId('unlock'),
        }));
        const pinEncrypted = new Uint8Array(await global.crypto.subtle.encrypt(
          {name: 'AES-GCM', iv: pinIv}, pinKey, pinProof,
        ));

        const nextCode = generateRecoveryCode();
        const nextSalt = global.crypto.getRandomValues(new Uint8Array(16));
        const nextIv = global.crypto.getRandomValues(new Uint8Array(12));
        const nextKey = await derivePinKey(normalizeRecoveryCode(nextCode), nextSalt);
        const nextProof = new TextEncoder().encode(JSON.stringify({
          scope,
          installation_id: profile.installation_id,
          marker: generateId('recovery'),
        }));
        const nextEncrypted = new Uint8Array(await global.crypto.subtle.encrypt(
          {name: 'AES-GCM', iv: nextIv}, nextKey, nextProof,
        ));

        Object.assign(profile, {
          salt: bytesToBase64(pinSalt), iv: bytesToBase64(pinIv), proof: bytesToBase64(pinEncrypted),
          recovery_salt: bytesToBase64(nextSalt), recovery_iv: bytesToBase64(nextIv),
          recovery_proof: bytesToBase64(nextEncrypted),
          recovery_generation: Number(profile.recovery_generation || 1) + 1,
          failed_attempts: 0, blocked_until: null,
          recovery_failed_attempts: 0, recovery_blocked_until: null,
        });
        await write(profile);
        db.close();
        return {...clonePlain(profile), recovery_code: nextCode};
      } catch (error) {
        if (recoveryVerified) {
          db.close();
          throw error;
        }
        profile.recovery_failed_attempts = Number(profile.recovery_failed_attempts || 0) + 1;
        if (profile.recovery_failed_attempts >= 5) {
          profile.recovery_blocked_until = new Date(now + 15 * 60 * 1000).toISOString();
          profile.recovery_failed_attempts = 0;
        }
        await write(profile);
        db.close();
        throw new Error('Codigo de emergencia incorreto.');
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
