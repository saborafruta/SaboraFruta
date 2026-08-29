(function () {
  'use strict';

  const STORAGE_KEY = 'ited.produtos.colunas.v1';
  const COLUMNS = [
    { key: 'id', width: 64, min: 52 },
    { key: 'nome', width: 330, min: 220, fixed: true },
    { key: 'codigo_barras', width: 132, min: 88 },
    { key: 'referencia', width: 84, min: 65 },
    { key: 'categoria', width: 140, min: 90 },
    { key: 'subcategoria', width: 142, min: 90 },
    { key: 'unidade', width: 58, min: 48 },
    { key: 'estoque', width: 96, min: 72 },
    { key: 'custo', width: 102, min: 76 },
    { key: 'preco', width: 116, min: 88 },
    { key: 'markup', width: 82, min: 68 },
    { key: 'margem', width: 82, min: 68 },
    { key: 'acoes', width: 90, min: 72 },
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
    const tableWidth = Math.max(visibleWidth, productScroller ? productScroller.clientWidth : 0);
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
      const url = new URL(window.location.href);
      url.searchParams.delete('page');
      url.searchParams.set('ver', 'todos');
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 60000);
      button.disabled = true;
      button.textContent = 'Carregando produtos…';
      status.textContent = ' — Buscando a lista completa, sem carregar fotos fora da tela.';
      container.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(url, {
          credentials: 'same-origin', signal: controller.signal,
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (!response.ok) throw new Error('Falha ao carregar');
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const rows = doc.querySelectorAll('.table-container tbody tr[data-product-id]');
        const pagination = doc.getElementById('produto-pagination');
        if (!pagination || doc.getElementById('produto-ver-todos')) throw new Error('Resposta inválida');
        const existing = Array.from(tbody.querySelectorAll('tr[data-product-id]'));
        const anchor = existing.find(row => row.getBoundingClientRect().bottom > 0);
        const anchorTop = anchor ? anchor.getBoundingClientRect().top : 0;
        const scrollX = window.scrollX;
        const scrollY = window.scrollY;
        const horizontal = scroller.scrollLeft;
        const byId = new Map(existing.map(row => [row.dataset.productId, row]));
        const fragment = document.createDocumentFragment();
        rows.forEach(row => fragment.appendChild(byId.get(row.dataset.productId) || document.importNode(row, true)));
        if (!rows.length) {
          const empty = doc.querySelector('.table-container tbody');
          fragment.append(...Array.from(empty.children, row => document.importNode(row, true)));
        }
        tbody.replaceChildren(fragment);
        document.getElementById('produto-pagination').replaceWith(document.importNode(pagination, true));
        const form = document.getElementById('produto-list-filters');
        if (!form.querySelector('[name="ver"]')) {
          const input = document.createElement('input');
          input.type = 'hidden'; input.name = 'ver'; input.value = 'todos';
          form.appendChild(input);
        }
        container.querySelectorAll('thead a[href]').forEach(link => {
          const sortUrl = new URL(link.href);
          sortUrl.searchParams.set('ver', 'todos');
          link.href = sortUrl.href;
        });
        applyColumnPreferences();
        history.replaceState(history.state, '', url);
        const restore = () => {
          scroller.scrollLeft = horizontal;
          const targetY = anchor && anchor.isConnected
            ? window.scrollY + anchor.getBoundingClientRect().top - anchorTop : scrollY;
          window.scrollTo({ left: scrollX, top: targetY, behavior: 'instant' });
        };
        restore();
        requestAnimationFrame(restore);
        document.getElementById('produto-load-status').textContent = ' — Lista completa carregada.';
      } catch (error) {
        status.textContent = ' Não foi possível carregar. Tente novamente.';
        button.disabled = false;
        button.textContent = 'Ver todos';
      } finally {
        clearTimeout(timeout);
        container.removeAttribute('aria-busy');
      }
    });
  }

  setupColumns();
  setupViewAll();
})();
