(function () {
  'use strict';

  const STORAGE_PREFIX = 'ited.tabelas.colunas.v1.';
  const MIN_WIDTH = 48;
  const DEFAULT_WIDTH = 140;
  const instances = new WeakMap();
  let openInstance = null;
  const serverPreferencesNode = document.getElementById('erp-table-preferences');
  const preferencesEndpoint = document.querySelector('meta[name="erp-table-preferences-url"]')?.content || '';
  const csrfToken = document.querySelector('meta[name="erp-csrf-token"]')?.content || '';
  const tableUserId = document.querySelector('meta[name="erp-table-user-id"]')?.content || 'anonymous';
  let serverPreferences = {};

  try {
    serverPreferences = JSON.parse(serverPreferencesNode?.textContent || '{}');
  } catch (error) {
    serverPreferences = {};
  }

  function slug(value) {
    return String(value || '')
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'coluna';
  }

  function shortHash(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36);
  }

  function directHeaderCells(table) {
    const row = table.tHead?.rows?.[0];
    if (!row) return [];
    const cells = Array.from(row.cells);
    if (cells.length < 2) return [];
    if (cells.some(cell => cell.colSpan !== 1 || cell.rowSpan !== 1)) return [];
    return cells;
  }

  function eligible(table) {
    if (table.dataset.columns === 'off' || table.dataset.erpTableColumnsReady) return false;
    if (table.closest('template, [role="dialog"], dialog, .modal, [data-columns="off"]')) return false;
    if (table.querySelector('[data-product-column]')) return false;
    if (!table.tBodies.length) return false;
    return directHeaderCells(table).length > 1;
  }

  function normalizePreferences(raw, columns) {
    const valid = new Set(columns.map(column => column.key));
    const hidden = Array.isArray(raw?.hidden) ? raw.hidden.filter(item => valid.has(item)) : [];
    const widths = {};
    if (raw?.widths && typeof raw.widths === 'object') {
      columns.forEach(column => {
        const width = Number(raw.widths[column.key]);
        if (Number.isFinite(width)) widths[column.key] = Math.max(MIN_WIDTH, Math.round(width));
      });
    }
    return { hidden, widths };
  }

  function readPreferences(storageKey, legacyStorageKey, preferenceKey, columns) {
    try {
      if (Object.prototype.hasOwnProperty.call(serverPreferences, preferenceKey)) {
        return normalizePreferences(serverPreferences[preferenceKey], columns);
      }
      const saved = localStorage.getItem(storageKey) || localStorage.getItem(legacyStorageKey) || '{}';
      return normalizePreferences(JSON.parse(saved), columns);
    } catch (error) {
      return { hidden: [], widths: {} };
    }
  }

  function save(instance) {
    try { localStorage.setItem(instance.storageKey, JSON.stringify(instance.preferences)); } catch (error) { /* modo privado */ }
    serverPreferences[instance.preferenceKey] = instance.preferences;
    if (!preferencesEndpoint || !csrfToken || csrfToken === 'NOTPROVIDED') return;
    clearTimeout(instance.saveTimer);
    instance.saveTimer = setTimeout(() => {
      fetch(preferencesEndpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({
          table: instance.preferenceKey,
          preferences: instance.preferences,
        }),
      }).catch(() => { /* a copia local continua disponivel */ });
    }, 180);
  }

  function cellAt(row, index, columnCount) {
    return row.cells.length === columnCount ? row.cells[index] : null;
  }

  function apply(instance) {
    const hidden = new Set(instance.preferences.hidden);
    if (hidden.size >= instance.columns.length) hidden.delete(instance.columns[0].key);
    instance.preferences.hidden = Array.from(hidden);
    let visibleWidth = 0;
    let visibleCount = 0;

    instance.columns.forEach(column => {
      const isHidden = hidden.has(column.key);
      const width = instance.preferences.widths[column.key] || column.defaultWidth;
      column.heading.hidden = isHidden;
      column.col.hidden = isHidden;
      column.col.style.width = isHidden ? '' : `${width}px`;
      column.heading.style.width = isHidden ? '' : `${width}px`;
      Array.from(instance.table.tBodies).forEach(body => {
        Array.from(body.rows).forEach(row => {
          const cell = cellAt(row, column.index, instance.columns.length);
          if (cell) cell.hidden = isHidden;
          if (row.cells.length === 1 && row.cells[0].colSpan > 1) {
            row.cells[0].colSpan = Math.max(1, instance.columns.length - hidden.size);
          }
        });
      });
      const checkbox = instance.panel.querySelector(`[data-column-key="${column.key}"]`);
      if (checkbox) checkbox.checked = !isHidden;
      if (!isHidden) {
        visibleWidth += width;
        visibleCount += 1;
      }
    });

    instance.panel.querySelectorAll('[data-column-key]').forEach(checkbox => {
      checkbox.disabled = checkbox.checked && visibleCount === 1;
    });
    instance.table.style.setProperty('--erp-table-visible-width', `${visibleWidth}px`);
    instance.table.dispatchEvent(new CustomEvent('erp:table-columns-applied', { bubbles: true }));
  }

  function positionPanel(instance) {
    if (!instance || instance.panel.hidden) return;
    const trigger = instance.trigger.getBoundingClientRect();
    const margin = 12;
    const width = Math.min(360, window.innerWidth - margin * 2);
    const left = Math.max(margin, Math.min(trigger.right - width, window.innerWidth - width - margin));
    instance.panel.style.left = `${left}px`;
    instance.panel.style.top = `${Math.min(trigger.bottom + 8, window.innerHeight - instance.panel.offsetHeight - margin)}px`;
  }

  function close(instance) {
    if (!instance) return;
    instance.panel.hidden = true;
    instance.trigger.setAttribute('aria-expanded', 'false');
    if (openInstance === instance) openInstance = null;
  }

  function open(instance) {
    if (openInstance && openInstance !== instance) close(openInstance);
    instance.panel.hidden = false;
    instance.trigger.setAttribute('aria-expanded', 'true');
    openInstance = instance;
    positionPanel(instance);
  }

  function createControls(instance) {
    const toolbar = document.createElement('div');
    toolbar.className = 'erp-table-columns-toolbar';
    toolbar.dataset.erpTableColumnsToolbar = '';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'btn-secondary';
    trigger.setAttribute('aria-expanded', 'false');
    trigger.innerHTML = '<svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16M8 4v4m8 2v4M10 16v4"/></svg><span>Colunas</span>';
    toolbar.appendChild(trigger);

    const panel = document.createElement('div');
    panel.className = 'erp-table-columns-picker';
    panel.hidden = true;
    panel.innerHTML = '<div class="erp-table-columns-heading"><strong>Colunas visíveis</strong><span>Marque o que deseja exibir. Arraste a divisória do cabeçalho para ajustar a largura.</span></div>';
    const options = document.createElement('div');
    options.className = 'erp-table-columns-options';
    instance.columns.forEach(column => {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.columnKey = column.key;
      const text = document.createElement('span');
      text.textContent = column.label;
      label.append(checkbox, text);
      options.appendChild(label);
    });
    const reset = document.createElement('button');
    reset.type = 'button';
    reset.className = 'erp-table-columns-reset';
    reset.textContent = 'Restaurar padrão';
    panel.append(options, reset);

    const tableContainer = instance.table.closest('.table-container');
    const scroller = instance.table.parentElement;
    const anchor = tableContainer || scroller;
    anchor.parentNode.insertBefore(toolbar, anchor);
    document.body.appendChild(panel);
    instance.toolbar = toolbar;
    instance.trigger = trigger;
    instance.panel = panel;

    trigger.addEventListener('click', event => {
      event.stopPropagation();
      panel.hidden ? open(instance) : close(instance);
    });
    panel.addEventListener('click', event => event.stopPropagation());
    options.addEventListener('change', event => {
      const checkbox = event.target.closest('[data-column-key]');
      if (!checkbox) return;
      const hidden = new Set(instance.preferences.hidden);
      checkbox.checked ? hidden.delete(checkbox.dataset.columnKey) : hidden.add(checkbox.dataset.columnKey);
      instance.preferences.hidden = Array.from(hidden);
      save(instance);
      apply(instance);
    });
    reset.addEventListener('click', () => {
      instance.preferences = { hidden: [], widths: {} };
      save(instance);
      apply(instance);
    });
  }

  function addResizers(instance) {
    instance.columns.forEach(column => {
      if (column.heading.querySelector('.erp-table-column-resizer')) return;
      const handle = document.createElement('span');
      handle.className = 'erp-table-column-resizer';
      handle.title = 'Arraste para ajustar; clique duas vezes para restaurar';
      handle.setAttribute('aria-hidden', 'true');
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const startX = event.clientX;
        const startWidth = column.heading.getBoundingClientRect().width;
        handle.classList.add('is-resizing');
        document.body.classList.add('erp-table-column-resizing');
        const move = moveEvent => {
          instance.preferences.widths[column.key] = Math.max(MIN_WIDTH, Math.round(startWidth + moveEvent.clientX - startX));
          apply(instance);
        };
        const stop = () => {
          handle.classList.remove('is-resizing');
          document.body.classList.remove('erp-table-column-resizing');
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', stop);
          document.removeEventListener('pointercancel', stop);
          save(instance);
        };
        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', stop);
        document.addEventListener('pointercancel', stop);
      });
      handle.addEventListener('dblclick', event => {
        event.preventDefault();
        event.stopPropagation();
        delete instance.preferences.widths[column.key];
        save(instance);
        apply(instance);
      });
      column.heading.appendChild(handle);
    });
  }

  function setup(table) {
    if (!eligible(table)) return;
    const headings = directHeaderCells(table);
    const duplicateLabels = {};
    const columns = headings.map((heading, index) => {
      const label = heading.innerText.trim().replace(/\s+/g, ' ') || `Coluna ${index + 1}`;
      const base = slug(label);
      duplicateLabels[base] = (duplicateLabels[base] || 0) + 1;
      const key = duplicateLabels[base] === 1 ? base : `${base}-${duplicateLabels[base]}`;
      const measured = Math.round(heading.getBoundingClientRect().width);
      const declared = Number(heading.dataset.columnWidth);
      const defaultWidth = Number.isFinite(declared) && declared > 0
        ? Math.max(MIN_WIDTH, Math.round(declared))
        : Math.max(MIN_WIDTH, measured || DEFAULT_WIDTH);
      return { index, key, label, heading, defaultWidth, col: null };
    });
    const signature = columns.map(column => column.key).join('|');
    const documentIndex = Array.from(document.querySelectorAll('table')).indexOf(table);
    const identity = table.id || table.dataset.tableKey || `${Math.max(0, documentIndex)}-${shortHash(signature)}`;
    const preferenceKey = `${location.pathname}.${identity}`;
    const legacyStorageKey = `${STORAGE_PREFIX}${location.pathname}.${identity}`;
    const storageKey = `${STORAGE_PREFIX}user-${tableUserId}.${location.pathname}.${identity}`;

    let colgroup = table.querySelector(':scope > colgroup[data-erp-columns]');
    if (!colgroup) {
      colgroup = document.createElement('colgroup');
      colgroup.dataset.erpColumns = '';
      table.insertBefore(colgroup, table.firstChild);
    }
    colgroup.replaceChildren(...columns.map(() => document.createElement('col')));
    columns.forEach((column, index) => { column.col = colgroup.children[index]; });

    const instance = {
      table,
      columns,
      storageKey,
      legacyStorageKey,
      preferenceKey,
      preferences: readPreferences(storageKey, legacyStorageKey, preferenceKey, columns),
    };
    let shouldMigrate = false;
    try {
      shouldMigrate = !Object.prototype.hasOwnProperty.call(serverPreferences, preferenceKey)
        && !localStorage.getItem(storageKey)
        && Boolean(localStorage.getItem(legacyStorageKey));
    } catch (error) { /* modo privado */ }
    table.dataset.erpTableColumnsReady = '1';
    table.classList.add('erp-configurable-table');
    instances.set(table, instance);
    createControls(instance);
    addResizers(instance);
    apply(instance);
    if (shouldMigrate) {
      save(instance);
      try { localStorage.removeItem(legacyStorageKey); } catch (error) { /* modo privado */ }
    }
  }

  function setupAll(root) {
    const tables = [];
    if (root instanceof HTMLTableElement) tables.push(root);
    if (root.querySelectorAll) tables.push(...root.querySelectorAll('table'));
    tables.forEach(table => setup(table));
  }

  document.addEventListener('click', () => close(openInstance));
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(openInstance); });
  window.addEventListener('resize', () => positionPanel(openInstance));
  window.addEventListener('scroll', () => positionPanel(openInstance), true);
  document.addEventListener('DOMContentLoaded', () => {
    setupAll(document);
    new MutationObserver(mutations => {
      mutations.forEach(mutation => mutation.addedNodes.forEach(node => {
        if (node.nodeType === Node.ELEMENT_NODE) setupAll(node);
      }));
    }).observe(document.body, { childList: true, subtree: true });
  });
})();
