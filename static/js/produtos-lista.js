(function () {
  'use strict';

  const STORAGE_KEY = 'ited.produtos.colunas.v1';
  const COLUMNS = [
    { key: 'id', width: 64, min: 34 },
    { key: 'nome', width: 330, min: 140, fixed: true },
    { key: 'codigo_barras', width: 132, min: 42 },
    { key: 'referencia', width: 84, min: 36 },
    { key: 'categoria', width: 140, min: 48 },
    { key: 'subcategoria', width: 142, min: 48 },
    { key: 'unidade', width: 58, min: 28 },
    { key: 'estoque', width: 96, min: 40 },
    { key: 'custo', width: 102, min: 48 },
    { key: 'preco', width: 116, min: 54 },
    { key: 'markup', width: 82, min: 44 },
    { key: 'margem', width: 82, min: 44 },
    { key: 'acoes', width: 90, min: 54 },
  ];
  const columnByKey = new Map(COLUMNS.map(column => [column.key, column]));
  let preferences = { hidden: [], widths: {} };
  let productTable = null;
  let productScroller = null;

  function readPreferences() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      const hidden = Array.isArray(saved.hidden)
        ? saved.hidden.filter(key => columnByKey.has(key) && !columnByKey.get(key).fixed)
        : [];
      const widths = {};
      if (saved.widths && typeof saved.widths === 'object') {
        COLUMNS.forEach(column => {
          const width = Number(saved.widths[column.key]);
          if (Number.isFinite(width)) widths[column.key] = Math.max(column.min, Math.round(width));
        });
      }
      return { hidden, widths };
    } catch (error) {
      return { hidden: [], widths: {} };
    }
  }

  function savePreferences() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch (error) { /* private mode */ }
  }

  function columnWidth(column) {
    return preferences.widths[column.key] || column.width;
  }

  function applyColumnPreferences() {
    if (!productTable) return;
    const hidden = new Set(preferences.hidden);
    COLUMNS.forEach(column => {
      const shouldHide = hidden.has(column.key);
      productTable.querySelectorAll(`[data-product-column="${column.key}"]`).forEach(cell => {
        cell.hidden = shouldHide;
      });
      const checkbox = document.querySelector(`[data-product-column-toggle="${column.key}"]`);
      if (checkbox) checkbox.checked = !shouldHide;
      const heading = productTable.querySelector(`th[data-product-column="${column.key}"]`);
      if (heading && !shouldHide) heading.style.width = `${columnWidth(column)}px`;
    });
    const visibleWidth = COLUMNS.reduce((sum, column) => (
      hidden.has(column.key) ? sum : sum + columnWidth(column)
    ), 0);
    const tableWidth = visibleWidth;
    productTable.style.width = `${tableWidth}px`;
    productTable.style.minWidth = `${tableWidth}px`;
    document.dispatchEvent(new CustomEvent('produto:columns-applied'));
  }

  function closeColumnPicker() {
    const panel = document.querySelector('[data-product-column-picker-panel]');
    const trigger = document.querySelector('[data-product-column-picker-trigger]');
    if (panel) panel.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  }

  function setupColumnPicker() {
    const trigger = document.querySelector('[data-product-column-picker-trigger]');
    const panel = document.querySelector('[data-product-column-picker-panel]');
    if (!trigger || !panel) return;
    trigger.addEventListener('click', event => {
      event.stopPropagation();
      panel.hidden = !panel.hidden;
      trigger.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
    });
    panel.addEventListener('click', event => event.stopPropagation());
    panel.querySelectorAll('[data-product-column-toggle]').forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        const key = checkbox.dataset.productColumnToggle;
        const column = columnByKey.get(key);
        if (!column || column.fixed) return;
        const hidden = new Set(preferences.hidden);
        checkbox.checked ? hidden.delete(key) : hidden.add(key);
        preferences.hidden = Array.from(hidden);
        savePreferences();
        applyColumnPreferences();
      });
    });
    panel.querySelector('[data-product-column-reset]')?.addEventListener('click', () => {
      preferences = { hidden: [], widths: {} };
      savePreferences();
      applyColumnPreferences();
    });
    document.addEventListener('click', closeColumnPicker);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeColumnPicker();
    });
  }

  function setupColumnResizers() {
    if (!productTable) return;
    productTable.querySelectorAll('th[data-product-column]').forEach(heading => {
      const key = heading.dataset.productColumn;
      const column = columnByKey.get(key);
      if (!column || heading.querySelector('.produto-column-resizer')) return;
      const handle = document.createElement('span');
      handle.className = 'produto-column-resizer';
      handle.setAttribute('aria-hidden', 'true');
      handle.title = 'Arraste para ajustar a largura; clique duas vezes para restaurar';
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const startX = event.clientX;
        const startWidth = heading.getBoundingClientRect().width;
        handle.classList.add('is-resizing');
        document.body.classList.add('produto-column-resizing');
        const move = moveEvent => {
          preferences.widths[key] = Math.max(column.min, Math.round(startWidth + moveEvent.clientX - startX));
          applyColumnPreferences();
        };
        const stop = () => {
          handle.classList.remove('is-resizing');
          document.body.classList.remove('produto-column-resizing');
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', stop);
          document.removeEventListener('pointercancel', stop);
          savePreferences();
        };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', stop);
        document.addEventListener('pointercancel', stop);
      });
      handle.addEventListener('dblclick', event => {
        event.preventDefault();
        event.stopPropagation();
        delete preferences.widths[key];
        savePreferences();
        applyColumnPreferences();
      });
      heading.appendChild(handle);
    });
  }

  function setupColumns() {
    const container = document.querySelector('.table-container');
    productTable = container?.querySelector('table') || null;
    productScroller = container?.querySelector('.overflow-x-auto') || null;
    if (!productTable) return;
    preferences = readPreferences();
    setupColumnPicker();
    setupColumnResizers();
    applyColumnPreferences();
    window.addEventListener('resize', applyColumnPreferences);
  }

  function setupViewAll() {
    const button = document.getElementById('produto-ver-todos');
    if (!button) return;
    button.addEventListener('click', async function () {
      if (button.disabled) return;
      const container = button.closest('.table-container');
      const tbody = container.querySelector('tbody');
      const scroller = container.querySelector('.overflow-x-auto');
      const status = document.getElementById('produto-load-status');
      const pagination = document.getElementById('produto-pagination');
      const currentPage = Number(pagination?.dataset.currentPage || 1);
      const totalPages = Number(pagination?.dataset.totalPages || 1);
      const totalCount = Number(pagination?.dataset.totalCount || 0);
      const pages = Array.from({ length: totalPages }, (_, index) => index + 1)
        .filter(page => page !== currentPage);
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 120000);
      button.disabled = true;
      button.textContent = 'Carregando produtos…';
      status.textContent = ` — Carregando página 1 de ${totalPages}…`;
      container.setAttribute('aria-busy', 'true');
      try {
        const existing = Array.from(tbody.querySelectorAll('tr[data-product-id]'));
        const anchor = existing.find(row => row.getBoundingClientRect().bottom > 0);
        const anchorTop = anchor ? anchor.getBoundingClientRect().top : 0;
        const scrollX = window.scrollX;
        const scrollY = window.scrollY;
        const horizontal = scroller.scrollLeft;
        const rowsByPage = new Map([[currentPage, existing]]);
        let nextPageIndex = 0;
        let loadedPages = 1;

        const loadPage = async pageNumber => {
          const url = new URL(window.location.href);
          url.searchParams.delete('ver');
          url.searchParams.set('page', String(pageNumber));
          url.searchParams.set('carregar_lote', '1');
          const response = await fetch(url, {
            credentials: 'same-origin',
            signal: controller.signal,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
          });
          if (!response.ok) throw new Error(`Falha ao carregar a página ${pageNumber}`);
          const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
          const rows = Array.from(doc.querySelectorAll('.table-container tbody tr[data-product-id]'));
          if (!rows.length && totalCount) throw new Error(`Resposta inválida na página ${pageNumber}`);
          rowsByPage.set(pageNumber, rows);
          loadedPages += 1;
          status.textContent = ` — Carregando página ${loadedPages} de ${totalPages}…`;
        };

        const worker = async () => {
          while (nextPageIndex < pages.length) {
            const pageNumber = pages[nextPageIndex];
            nextPageIndex += 1;
            await loadPage(pageNumber);
          }
        };
        await Promise.all(Array.from({ length: Math.min(2, pages.length) }, () => worker()));

        const fragment = document.createDocumentFragment();
        for (let pageNumber = 1; pageNumber <= totalPages; pageNumber += 1) {
          (rowsByPage.get(pageNumber) || []).forEach(row => {
            fragment.appendChild(row.isConnected ? row : document.importNode(row, true));
          });
        }
        tbody.replaceChildren(fragment);
        const summary = document.createElement('div');
        summary.textContent = `Todos os ${totalCount} produtos — Lista completa carregada.`;
        pagination.replaceChildren(summary);
        applyColumnPreferences();
        const restore = () => {
          scroller.scrollLeft = horizontal;
          const targetY = anchor && anchor.isConnected
            ? window.scrollY + anchor.getBoundingClientRect().top - anchorTop : scrollY;
          window.scrollTo({ left: scrollX, top: targetY, behavior: 'instant' });
        };
        restore();
        requestAnimationFrame(restore);
      } catch (error) {
        controller.abort();
        status.textContent = error.name === 'AbortError'
          ? ' — O carregamento demorou demais. Tente novamente.'
          : ' — Não foi possível carregar. Tente novamente.';
        button.disabled = false;
        button.textContent = 'Ver todos';
      } finally {
        clearTimeout(timeout);
        container.removeAttribute('aria-busy');
      }
    });
  }

  const ACTIVE_ICON = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
  const INACTIVE_ICON = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';

  function updateToggleRow(form, data) {
    const row = form.closest('tr[data-product-id]');
    const statusFilter = document.querySelector('#produto-list-filters [name="status"]')?.value || 'todos';
    const stockOnly = document.querySelector('#produto-list-filters [name="com_estoque"]')?.checked || false;
    const mustLeaveList = (statusFilter === 'ativo' && !data.active)
      || (statusFilter === 'inativo' && data.active)
      || (stockOnly && Number(data.current_stock) <= 0);
    if (mustLeaveList) {
      row?.remove();
      return;
    }
    form.dataset.productActive = data.active ? '1' : '0';
    form.dataset.currentStock = String(data.current_stock);
    form.dataset.currentStockDisplay = data.current_stock_display;
    const button = form.querySelector('button[type="submit"]');
    if (button) {
      button.disabled = false;
      button.style.color = data.active ? '#4ade80' : '#f87171';
      button.title = data.active ? 'Desativar' : 'Ativar';
      button.innerHTML = data.active ? ACTIVE_ICON : INACTIVE_ICON;
    }
    row?.classList.toggle('produto-inactive-row', !data.active);
    const nameStatus = row?.querySelector('.produto-name-status');
    let badge = nameStatus?.querySelector('.produto-status-inactive');
    if (!data.active && nameStatus && !badge) {
      badge = document.createElement('span');
      badge.className = 'produto-status-inactive';
      badge.textContent = 'Inativo';
      nameStatus.appendChild(badge);
    } else if (data.active) {
      badge?.remove();
    }
    const stockCell = row?.querySelector('[data-field="estoque_atual"]');
    if (stockCell) {
      stockCell.dataset.value = String(data.current_stock);
      const display = stockCell.querySelector('.inline-display');
      if (display) display.textContent = data.current_stock_display;
    }
  }

  function setupToggleWithoutReload() {
    document.addEventListener('submit', async event => {
      const form = event.target.closest('[data-produto-toggle-form]');
      if (!form || form.dataset.stockDecision === 'ready') return;
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]');
      if (button?.disabled) return;
      if (button) button.disabled = true;
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
          body: new FormData(form),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || 'Não foi possível alterar o produto.');
        updateToggleRow(form, data);
        const status = document.getElementById('produto-load-status');
        if (status) status.textContent = ` — ${data.message}`;
      } catch (error) {
        if (button) button.disabled = false;
        window.alert(error.message || 'Não foi possível alterar o produto.');
      }
    });
  }

  setupColumns();
  setupViewAll();
  setupToggleWithoutReload();
})();
