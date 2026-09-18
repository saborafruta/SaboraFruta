(function () {
  'use strict';

  const MAX_CATALOG_AGE = 12 * 60 * 60 * 1000;
  const blockedPaymentTypes = ['boleto', 'vale', 'cashback', 'crediario', 'convenio'];
  const state = {profiles: [], selectedScope: '', profile: null, store: null, snapshot: null, sale: null, draftMode: 'venda', query: ''};
  const el = id => document.getElementById(id);
  const money = value => Number(value || 0).toLocaleString('pt-BR', {style: 'currency', currency: 'BRL'});
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const nowIso = () => new Date().toISOString();

  function toast(message) {
    el('toast').textContent = message;
    el('toast').classList.remove('hidden');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el('toast').classList.add('hidden'), 3500);
  }

  function blankSale() {
    return {
      id: null, numero: null, local_id: state.store.newSaleId(),
      cliente: {id: null, nome: 'Consumidor Final', cpf_cnpj: ''},
      itens: [], pagamentos: [], subtotal: 0, desconto: 0, acrescimo: 0,
      total: 0, valor_pago: 0, restante: 0, delivery: false,
      endereco_entrega: {}, fora_estabelecimento: false, viagem_id: null,
      observacao: '', comanda_id_origem: null,
    };
  }

  function recalculate() {
    const sale = state.sale;
    sale.itens.forEach(item => { item.valor_total = Math.round(Number(item.quantidade) * Number(item.valor_unitario) * 100) / 100; });
    sale.subtotal = sale.itens.reduce((sum, item) => sum + Number(item.valor_total || 0), 0);
    sale.total = sale.subtotal;
    sale.valor_pago = sale.pagamentos.reduce((sum, payment) => sum + Number(payment.valor || 0), 0);
    sale.restante = Math.max(0, Math.round((sale.total - sale.valor_pago) * 100) / 100);
  }

  async function persistDraft() {
    if (!state.store || !state.sale) return;
    if (!state.sale.itens.length && !state.sale.pagamentos.length) return state.store.deleteDraft();
    await state.store.saveDraft({
      version: 1, status: 'rascunho', venda: state.sale, modo: 'venda',
      sessao_id: state.snapshot?.sessao?.id || null,
      caixa_id: state.snapshot?.sessao?.caixa_id || null,
      credito_aplicado: 0, data_venda_retroativa: '', venda_edicao_origem_id: null,
      venda_edicao_origem_numero: null,
    });
  }

  function renderProfiles() {
    el('profiles').innerHTML = state.profiles.length
      ? state.profiles.map(profile => `<button class="profile ${profile.scope === state.selectedScope ? 'selected' : ''}" data-scope="${escapeHtml(profile.scope)}" type="button"><strong>${escapeHtml(profile.usuario_nome)}</strong><div class="sub">${escapeHtml(profile.filial_nome)} · autorizado até ${new Date(profile.valid_until).toLocaleString('pt-BR')}</div></button>`).join('')
      : '<div class="notice">Nenhum perfil autorizado. Conecte este computador, abra o PDV e ative a abertura offline.</div>';
    document.querySelectorAll('.profile').forEach(button => button.addEventListener('click', () => {
      state.selectedScope = button.dataset.scope;
      renderProfiles();
      el('pin').focus();
    }));
  }

  async function loadProfiles() {
    try {
      state.profiles = await window.PDVLocalStore.listOfflineProfiles();
      state.selectedScope = state.profiles[0]?.scope || '';
      renderProfiles();
    } catch (error) {
      el('lock-error').textContent = error.message || 'Não foi possível abrir o armazenamento local.';
    }
  }

  async function unlock() {
    el('lock-error').textContent = '';
    const pin = el('pin').value;
    if (!state.selectedScope) return void (el('lock-error').textContent = 'Selecione um perfil autorizado.');
    if (!/^\d{6}$/.test(pin)) return void (el('lock-error').textContent = 'Informe os 6 números do PIN local.');
    try {
      state.profile = await window.PDVLocalStore.unlockOfflineProfile(state.selectedScope, pin);
      state.store = await new window.PDVLocalStore({filialId: state.profile.filial_id, usuarioId: state.profile.usuario_id}).init();
      if (state.store.installationId !== state.profile.installation_id) throw new Error('Esta autorização pertence a outra instalação.');
      state.snapshot = await state.store.loadSnapshot();
      if (!state.snapshot?.produtos?.length) throw new Error('Não existe catálogo local disponível neste computador.');
      if (!state.snapshot?.sessao?.id) throw new Error('Não existe uma sessão de caixa aberta salva neste computador.');
      const draft = await state.store.loadDraft();
      state.sale = draft?.venda || blankSale();
      state.draftMode = draft?.modo || 'venda';
      state.sale.local_id ||= state.store.newSaleId();
      recalculate();
      el('pin').value = '';
      el('lock').classList.add('hidden');
      el('app').classList.remove('hidden');
      el('context').textContent = `${state.profile.filial_nome} · ${state.profile.usuario_nome}`;
      renderAll();
      el('search').focus();
    } catch (error) {
      el('lock-error').textContent = error.message || 'Não foi possível desbloquear o PDV.';
      el('pin').select();
    }
  }

  function toggleRecovery() {
    el('recovery').classList.toggle('hidden');
    el('new-recovery').classList.add('hidden');
    el('lock-error').textContent = '';
    if (!el('recovery').classList.contains('hidden')) el('recovery-code').focus();
  }

  async function recoverPin() {
    el('lock-error').textContent = '';
    if (!state.selectedScope) return void (el('lock-error').textContent = 'Selecione um perfil autorizado.');
    const newPin = el('new-pin').value;
    if (!/^\d{6}$/.test(newPin)) return void (el('lock-error').textContent = 'Crie um novo PIN com 6 números.');
    if (newPin !== el('new-pin-confirm').value) return void (el('lock-error').textContent = 'Os novos PINs não são iguais.');
    try {
      const profile = await window.PDVLocalStore.recoverOfflineProfile(
        state.selectedScope, el('recovery-code').value, newPin,
      );
      el('new-recovery-code').textContent = profile.recovery_code;
      el('new-recovery').classList.remove('hidden');
      el('recovery').classList.add('hidden');
      el('pin').value = '';
      el('recovery-code').value = el('new-pin').value = el('new-pin-confirm').value = '';
      el('lock-error').textContent = 'PIN alterado. Guarde o novo código e entre com o novo PIN.';
    } catch (error) {
      el('lock-error').textContent = error.message || 'Não foi possível recuperar o PIN.';
    }
  }

  function downloadRecoveryCode() {
    const code = el('new-recovery-code').textContent.trim();
    if (!code) return;
    const content = `Código de emergência do PDV: ${code}\n\nGuarde com o responsável. Uso único: ao utilizá-lo, outro código será criado.\n`;
    const url = URL.createObjectURL(new Blob([content], {type: 'text/plain;charset=utf-8'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'codigo-emergencia-pdv.txt';
    link.click();
    URL.revokeObjectURL(url);
  }

  function catalogValid() {
    const reference = state.snapshot?.catalogo_em || state.snapshot?.gerado_em;
    const age = reference ? Date.now() - new Date(reference).getTime() : Infinity;
    return Number.isFinite(age) && age >= 0 && age <= MAX_CATALOG_AGE;
  }

  function safePayments() {
    return (state.snapshot?.formas_pagamento || []).filter(payment => {
      const type = String(payment.tipo || '').toLowerCase();
      return !payment.requer_tef && !blockedPaymentTypes.includes(type);
    });
  }

  function operationBlockReason() {
    if (state.draftMode !== 'venda') return 'Orçamento e bonificação precisam ser retomados com internet.';
    if (state.sale?.cliente?.id) return 'Venda com cliente identificado precisa ser retomada com internet.';
    if (state.sale?.delivery || state.sale?.fora_estabelecimento || state.sale?.comanda_id_origem) {
      return 'Delivery, venda fora e comanda precisam ser retomados com internet.';
    }
    const allowed = new Set(safePayments().map(payment => String(payment.id)));
    if ((state.sale?.pagamentos || []).some(payment => !allowed.has(String(payment.forma_id)))) {
      return 'O carrinho contém uma forma de pagamento que exige validação online.';
    }
    return '';
  }

  function searchable(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  function visibleProducts() {
    const tokens = searchable(state.query).trim().split(/\s+/).filter(Boolean);
    return (state.snapshot?.produtos || []).filter(product => {
      if (!tokens.length) return true;
      const text = searchable([product.id, product.codigo, product.codigo_barras, product.descricao].join(' '));
      return tokens.every(token => text.includes(token));
    }).slice(0, 80);
  }

  function unitPrice(product) {
    return Number(product.preco_base ?? product.preco ?? 0);
  }

  function quantityStep(product) {
    const informed = Number(product.quantidade_step || 0);
    const fractional = product.fracionavel || informed > 0 && informed < 1
      || ['fracionado', 'granel_peso', 'granel_volume', 'granel_metragem'].includes(product.tipo_produto);
    return fractional ? (informed > 0 ? informed : 0.001) : 1;
  }

  async function addProduct(id) {
    if (!catalogValid()) return toast('Catálogo vencido. Conecte o PDV antes de iniciar novas vendas.');
    const product = (state.snapshot.produtos || []).find(item => String(item.id) === String(id));
    if (!product) return;
    const existing = state.sale.itens.find(item => String(item.produto_id) === String(product.id));
    if (existing) existing.quantidade = Math.round((Number(existing.quantidade || 0) + quantityStep(existing)) * 1000) / 1000;
    else {
      const price = unitPrice(product);
      const step = quantityStep(product);
      state.sale.itens.push({
        _id: Date.now() + Math.random(), produto_id: product.id, descricao: product.descricao,
        codigo_barras: product.codigo_barras || '', estoque_disponivel: Number(product.estoque_disponivel || 0),
        tipo_produto: product.tipo_produto || '', fracionavel: step < 1, quantidade_step: step,
        quantidade_decimais: step < 1 ? 3 : 0, quantidade: step, valor_unitario: price, _precoOriginal: price,
        _precoTabela: price, _precoManual: false, preco_origem: 'Preço normal armazenado',
        preco_origem_tipo: 'normal', oferta_tipo: 'normal', tipo_venda: 'unitario', kit_id: null,
        desconto_percentual: 0, desconto_valor: 0, valor_total: price, obs: '', _showObs: false,
      });
    }
    recalculate();
    await persistDraft();
    renderAll();
  }

  async function changeQuantity(itemId, delta) {
    const item = state.sale.itens.find(row => String(row._id) === String(itemId));
    if (!item) return;
    item.quantidade = Math.round((Number(item.quantidade || 0) + delta * quantityStep(item)) * 1000) / 1000;
    if (item.quantidade <= 0) state.sale.itens = state.sale.itens.filter(row => row !== item);
    recalculate(); await persistDraft(); renderAll();
  }

  async function addPayment(id) {
    const payment = safePayments().find(item => String(item.id) === String(id));
    if (!payment || state.sale.restante <= 0.001) return;
    const value = state.sale.restante;
    state.sale.pagamentos.push({_id: Date.now() + Math.random(), forma_id: payment.id, forma_descricao: payment.descricao, forma_tipo: payment.tipo, valor: value, troco: 0, prazo_dias: null, bandeira: '', numero_parcelas: 1});
    recalculate(); await persistDraft(); renderAll();
  }

  async function removePayment(id) {
    state.sale.pagamentos = state.sale.pagamentos.filter(payment => String(payment._id) !== String(id));
    recalculate(); await persistDraft(); renderAll();
  }

  function renderProducts() {
    const products = visibleProducts();
    el('products').innerHTML = products.length ? products.map(product => `<button type="button" class="card product" data-id="${escapeHtml(product.id)}"><div class="desc">${escapeHtml(product.descricao)}</div><div class="sub">${escapeHtml(product.codigo_barras || product.codigo || '')} · estoque informado: ${escapeHtml(product.estoque_disponivel ?? '—')}</div><div class="price">${money(unitPrice(product))}</div></button>`).join('') : '<div class="empty">Nenhum produto encontrado.</div>';
    document.querySelectorAll('.product').forEach(button => button.addEventListener('click', () => addProduct(button.dataset.id)));
    const reference = state.snapshot?.catalogo_em || state.snapshot?.gerado_em;
    el('catalog-age').textContent = `${catalogValid() ? 'Catálogo válido' : 'CATÁLOGO VENCIDO'} · atualizado em ${reference ? new Date(reference).toLocaleString('pt-BR') : 'data desconhecida'}`;
    el('catalog-age').style.color = catalogValid() ? '#9ca3af' : '#f87171';
  }

  function renderCart() {
    el('cart').innerHTML = state.sale.itens.length ? state.sale.itens.map(item => `<div class="line"><div><div class="desc">${escapeHtml(item.descricao)}</div><div class="sub">${money(item.valor_unitario)} × ${escapeHtml(item.quantidade)} = ${money(item.valor_total)}</div></div><div class="qty"><button class="minus" data-id="${escapeHtml(item._id)}" type="button">−</button><strong>${escapeHtml(item.quantidade)}</strong><button class="plus" data-id="${escapeHtml(item._id)}" type="button">+</button></div></div>`).join('') : '<div class="empty">Carrinho vazio. Selecione produtos do catálogo local.</div>';
    document.querySelectorAll('.minus').forEach(button => button.addEventListener('click', () => changeQuantity(button.dataset.id, -1)));
    document.querySelectorAll('.plus').forEach(button => button.addEventListener('click', () => changeQuantity(button.dataset.id, 1)));
  }

  function renderPayments() {
    el('payments').innerHTML = safePayments().map(payment => `<button class="pay" data-id="${escapeHtml(payment.id)}" type="button" ${state.sale.restante <= 0.001 ? 'disabled' : ''}>${escapeHtml(payment.descricao)}</button>`).join('') || '<div class="notice">Nenhuma forma de pagamento segura está disponível offline.</div>';
    document.querySelectorAll('.pay').forEach(button => button.addEventListener('click', () => addPayment(button.dataset.id)));
    el('selected-payments').innerHTML = state.sale.pagamentos.map(payment => `<div class="line"><div><div class="desc">${escapeHtml(payment.forma_descricao)}</div><div class="sub">${money(payment.valor)}</div></div><button class="remove remove-payment" data-id="${escapeHtml(payment._id)}" type="button">×</button></div>`).join('');
    document.querySelectorAll('.remove-payment').forEach(button => button.addEventListener('click', () => removePayment(button.dataset.id)));
  }

  async function renderQueue() {
    const queue = await state.store.listQueuedSales();
    el('queue').textContent = queue.length ? `${queue.length} venda(s) aguardando sincronização neste perfil.` : 'Nenhuma venda aguardando sincronização.';
  }

  function renderTotals() {
    el('total').textContent = money(state.sale.total);
    el('paid').textContent = money(state.sale.valor_pago);
    el('remaining').textContent = money(state.sale.restante);
    const blocked = operationBlockReason();
    el('operation-warning').textContent = blocked;
    el('operation-warning').classList.toggle('hidden', !blocked);
    el('finish').disabled = !!blocked || !catalogValid() || !state.sale.itens.length || state.sale.restante > 0.001 || !state.snapshot?.sessao?.id;
  }

  function renderAll() { renderProducts(); renderCart(); renderPayments(); renderTotals(); renderQueue(); }

  async function finishSale() {
    if (el('finish').disabled) return;
    if (!window.confirm(`Salvar esta venda de ${money(state.sale.total)} na fila offline?`)) return;
    const payload = {
      idempotency_key: state.sale.local_id, sessao_id: state.snapshot.sessao.id,
      cliente_id: null, itens: state.sale.itens.map(item => {
        const safe = {...item};
        delete safe.custo_atual;
        delete safe.margem_percentual;
        return safe;
      }), pagamentos: state.sale.pagamentos,
      desconto: 0, acrescimo: 0, delivery: false, endereco_entrega: {},
      venda_fora_estabelecimento: false, viagem_id: null, credito_valor: 0,
      data_venda: null, observacao: state.sale.observacao || '', venda_edicao_origem_id: null,
      comanda_id: null, forcar_estoque_negativo: true,
    };
    try {
      await state.store.enqueueSale({local_id: state.sale.local_id, endpoint: '/pdv/api/venda/finalizar/', payload, status: 'pendente', last_error: '', created_at: nowIso()});
      await state.store.deleteDraft();
      state.sale = blankSale();
      state.draftMode = 'venda';
      recalculate();
      renderAll();
      toast('Venda protegida. Ela será enviada automaticamente quando o ERP voltar.');
    } catch (error) {
      toast(error.message || 'Não foi possível gravar a venda local.');
    }
  }

  async function clearSale() {
    if ((state.sale.itens.length || state.sale.pagamentos.length) && !window.confirm('Limpar este carrinho?')) return;
    await state.store.deleteDraft();
    state.sale = blankSale();
    state.draftMode = 'venda';
    recalculate(); renderAll();
  }

  function tryOnline() { window.location.href = '/pdv/'; }

  el('unlock').addEventListener('click', unlock);
  el('show-recovery').addEventListener('click', toggleRecovery);
  el('recover').addEventListener('click', recoverPin);
  el('download-recovery').addEventListener('click', downloadRecoveryCode);
  el('pin').addEventListener('keydown', event => { if (event.key === 'Enter') unlock(); });
  el('search').addEventListener('input', event => { state.query = event.target.value; renderProducts(); });
  el('search').addEventListener('keydown', event => {
    if (event.key !== 'Enter') return;
    const exact = (state.snapshot?.produtos || []).find(product => String(product.codigo_barras || '') === event.target.value.trim());
    if (exact) { event.preventDefault(); addProduct(exact.id); event.target.select(); }
  });
  el('finish').addEventListener('click', finishSale);
  el('clear').addEventListener('click', clearSale);
  el('go-online').addEventListener('click', tryOnline);
  window.addEventListener('online', () => { toast('Conexão recuperada. Voltando ao PDV para sincronizar…'); setTimeout(tryOnline, 900); });
  loadProfiles();
})();
