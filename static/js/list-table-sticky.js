(function () {
  'use strict';

  var queued = false;

  function cloneElement() {
    var clone = document.querySelector('.erp-table-sticky-clone');
    if (clone) return clone;

    clone = document.createElement('div');
    clone.className = 'erp-table-sticky-clone';
    clone.setAttribute('aria-hidden', 'true');
    clone.innerHTML = '<div class="erp-table-sticky-gap"></div><table><thead></thead></table>';
    document.body.appendChild(clone);
    return clone;
  }

  function hideClone() {
    var clone = document.querySelector('.erp-table-sticky-clone');
    if (clone) clone.classList.remove('is-visible');
  }

  function listTables() {
    var selector = '.erp-list-page .table-container table, table[data-sticky-list-table]';
    return Array.prototype.slice.call(document.querySelectorAll(selector)).map(function (table) {
      if (table.closest('[data-no-sticky-table], .erp-table-sticky-clone')) return null;

      var header = table.querySelector('thead tr');
      if (!header || !header.querySelector('th')) return null;

      var container = table.closest('.table-container, [data-sticky-list-container]');
      if (!container) container = table.parentElement;
      if (!container) return null;

      header.classList.add('table-header');
      container.classList.add('erp-sticky-table-container');
      return { table: table, header: header, container: container };
    }).filter(Boolean);
  }

  function appHeaderBottom() {
    var header = document.querySelector('.app-shell-content > header');
    if (!header) return 56;
    var rect = header.getBoundingClientRect();
    return Math.max(0, Math.ceil(rect.bottom));
  }

  function stickyGap() {
    var value = getComputedStyle(document.body).getPropertyValue('--erp-list-sticky-gap');
    var parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function render(item, top) {
    if (!item) {
      hideClone();
      return;
    }

    var clone = cloneElement();
    var containerRect = item.container.getBoundingClientRect();
    var tableRect = item.table.getBoundingClientRect();
    var viewportWidth = window.innerWidth || document.documentElement.clientWidth;
    var left = Math.max(containerRect.left, 0);
    var right = Math.min(containerRect.right, viewportWidth);
    if (right <= left || tableRect.width <= 0) {
      hideClone();
      return;
    }

    clone.style.top = top + 'px';
    clone.style.left = left + 'px';
    clone.style.width = (right - left) + 'px';

    var cloneTable = clone.querySelector('table');
    var cloneHead = clone.querySelector('thead');
    cloneTable.style.width = tableRect.width + 'px';
    cloneTable.style.marginLeft = (tableRect.left - left) + 'px';

    var row = document.createElement('tr');
    row.className = item.header.className;
    Array.prototype.slice.call(item.header.children).forEach(function (cell) {
      var copy = cell.cloneNode(true);
      var rect = cell.getBoundingClientRect();
      copy.style.width = rect.width + 'px';
      copy.style.minWidth = rect.width + 'px';
      copy.style.maxWidth = rect.width + 'px';
      row.appendChild(copy);
    });
    cloneHead.replaceChildren(row);
    clone.classList.add('is-visible');
  }

  function update() {
    if (!document.body || document.body.classList.contains('erp-embedded') ||
        !window.matchMedia('(min-width: 768px)').matches) {
      if (document.body) document.body.classList.remove('has-sticky-list');
      hideClone();
      return;
    }

    var items = listTables();
    document.body.classList.toggle('has-sticky-list', items.length > 0);
    if (!items.length) {
      hideClone();
      return;
    }

    var top = appHeaderBottom();
    var gap = stickyGap();
    var active = null;
    var activeTop = -Infinity;

    items.forEach(function (item) {
      var containerRect = item.container.getBoundingClientRect();
      var headerRect = item.header.getBoundingClientRect();
      var tableRect = item.table.getBoundingClientRect();
      var visible = containerRect.width > 0 && containerRect.height > 0 &&
        tableRect.width > 0 && tableRect.height > 0 &&
        getComputedStyle(item.container).display !== 'none';
      var isActive = visible && headerRect.top <= top + gap &&
        containerRect.bottom > top + headerRect.height + gap;

      item.container.classList.toggle('erp-sticky-active', isActive);
      if (isActive && containerRect.top > activeTop) {
        active = item;
        activeTop = containerRect.top;
      }
    });

    render(active, top);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(function () {
      queued = false;
      update();
    });
  }

  function init() {
    update();
    window.addEventListener('scroll', schedule, { passive: true });
    document.addEventListener('scroll', schedule, { passive: true, capture: true });
    window.addEventListener('resize', schedule);

    var main = document.querySelector('.app-shell-content main');
    if (main && window.MutationObserver) {
      new MutationObserver(schedule).observe(main, { childList: true, subtree: true });
    }
    if (document.body && window.MutationObserver) {
      new MutationObserver(schedule).observe(document.body, {
        attributes: true,
        attributeFilter: ['class']
      });
    }
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(schedule);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.erpUpdateStickyListHeaders = update;
})();
