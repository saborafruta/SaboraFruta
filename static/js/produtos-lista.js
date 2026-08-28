(function () {
  'use strict';
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
    button.textContent = 'Carregando…';
    status.textContent = '';
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
      // Capture the position just before insertion: the user may scroll while loading.
      const existing = Array.from(tbody.querySelectorAll('tr[data-product-id]'));
      const anchor = existing.find(row => row.getBoundingClientRect().bottom > 0);
      const anchorTop = anchor ? anchor.getBoundingClientRect().top : 0;
      const scrollX = window.scrollX;
      const scrollY = window.scrollY;
      const horizontal = scroller.scrollLeft;
      const byId = new Map(existing.map(row => [row.dataset.productId, row]));
      const fragment = document.createDocumentFragment();
      // Keep existing nodes (including any inline edits) and the server's sort order.
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
      history.replaceState(history.state, '', url);
      // Counteract browser scroll anchoring, including when loading from page 2+.
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
})();
