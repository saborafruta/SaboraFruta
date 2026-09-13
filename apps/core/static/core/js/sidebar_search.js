(function () {
  'use strict';

  function normalize(value) {
    return (value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  function pathFor(anchor) {
    try {
      var url = new URL(anchor.href, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname === '/auth/logout/') return '';
      return url.pathname + url.search;
    } catch (error) {
      return '';
    }
  }

  function labelFor(anchor) {
    var text = (anchor.textContent || '')
      .replace(/[★☆]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
    return text || (anchor.getAttribute('title') || '').trim();
  }

  function recordsFor(nav) {
    var seen = new Set();
    var records = [];
    Array.prototype.slice.call(nav.querySelectorAll('a[href]')).forEach(function (anchor) {
      if (anchor.closest('[data-sidebar-search-results]') || anchor.closest('.sidebar-favorites-panel')) return;
      var path = pathFor(anchor);
      var label = labelFor(anchor);
      if (!path || !label || seen.has(path)) return;
      seen.add(path);
      records.push({ path: path, label: label, search: normalize(label + ' ' + (anchor.title || '')) });
    });
    return records;
  }

  function startSearch(box) {
    if (box.dataset.ready === 'true') return;
    box.dataset.ready = 'true';
    var nav = box.closest('nav');
    // Garante a ordem logo -> busca -> favoritos mesmo se o navegador ainda
    // tiver uma versao anterior do script de favoritos em memoria.
    var logo = nav.querySelector('.sidebar-branch-logo-card');
    if (logo && logo.nextElementSibling !== box) logo.insertAdjacentElement('afterend', box);
    else if (!logo && nav.firstElementChild !== box) nav.prepend(box);
    var input = box.querySelector('[data-sidebar-search-input]');
    var results = box.querySelector('[data-sidebar-search-results]');
    var clear = box.querySelector('[data-sidebar-search-clear]');
    var selected = -1;
    var visibleRecords = [];

    function select(index) {
      var links = results.querySelectorAll('a');
      if (!links.length) return;
      selected = Math.max(0, Math.min(index, links.length - 1));
      links.forEach(function (link, itemIndex) {
        link.classList.toggle('is-selected', itemIndex === selected);
      });
      links[selected].scrollIntoView({ block: 'nearest' });
    }

    function closeResults() {
      results.hidden = true;
      results.innerHTML = '';
      selected = -1;
      visibleRecords = [];
    }

    function render() {
      var term = normalize(input.value);
      clear.hidden = !term;
      if (!term) {
        closeResults();
        return;
      }
      visibleRecords = recordsFor(nav).filter(function (record) {
        return record.search.indexOf(term) !== -1;
      }).sort(function (a, b) {
        var aStarts = normalize(a.label).indexOf(term) === 0;
        var bStarts = normalize(b.label).indexOf(term) === 0;
        if (aStarts !== bStarts) return aStarts ? -1 : 1;
        return a.label.localeCompare(b.label, 'pt-BR');
      });

      results.innerHTML = '';
      if (!visibleRecords.length) {
        var empty = document.createElement('div');
        empty.className = 'sidebar-menu-search-empty';
        empty.textContent = 'Nenhuma função encontrada';
        results.appendChild(empty);
      } else {
        visibleRecords.forEach(function (record) {
          var link = document.createElement('a');
          link.href = record.path;
          link.textContent = record.label;
          link.title = record.label;
          results.appendChild(link);
        });
      }
      results.hidden = false;
      selected = -1;
    }

    input.addEventListener('input', render);
    input.addEventListener('focus', render);
    input.addEventListener('keydown', function (event) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        select(selected + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        select(selected <= 0 ? visibleRecords.length - 1 : selected - 1);
      } else if (event.key === 'Enter' && visibleRecords.length) {
        event.preventDefault();
        window.location.href = visibleRecords[selected >= 0 ? selected : 0].path;
      } else if (event.key === 'Escape') {
        input.value = '';
        render();
        input.blur();
      }
    });
    clear.addEventListener('click', function () {
      input.value = '';
      render();
      input.focus();
    });
    document.addEventListener('click', function (event) {
      if (!box.contains(event.target)) closeResults();
    });
  }

  function start() {
    document.querySelectorAll('[data-sidebar-search]').forEach(startSearch);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
