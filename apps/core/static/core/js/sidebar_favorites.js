(function () {
  'use strict';

  function csrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  function pathFor(anchor) {
    try {
      var url = new URL(anchor.href, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith('/')) return '';
      if (url.pathname === '/auth/logout/') return '';
      return url.pathname;
    } catch (error) {
      return '';
    }
  }

  function labelFor(anchor) {
    var explicit = (anchor.getAttribute('title') || '').trim();
    if (explicit) return explicit;
    return (anchor.textContent || '')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/^[\u2713\u2605\u2606]\s*/, '');
  }

  function initialFavorites() {
    var element = document.getElementById('sidebar-favorites-data');
    if (!element) return [];
    try {
      var parsed = JSON.parse(element.textContent || '[]');
      return Array.isArray(parsed) ? parsed.filter(function (item) {
        return typeof item === 'string' && item.startsWith('/');
      }) : [];
    } catch (error) {
      return [];
    }
  }

  function readJsonResponse(response) {
    var contentType = response.headers.get('content-type') || '';
    if (contentType.indexOf('application/json') === -1) {
      throw new Error(
        response.status === 401 || response.status === 403
          ? 'Sua sessao expirou. Recarregue a pagina e tente novamente.'
          : 'O servidor ficou indisponivel por alguns instantes. Tente novamente.'
      );
    }
    return response.json().then(function (data) {
      return { response: response, data: data };
    });
  }

  function start() {
    var root = document.getElementById('sidebar-root');
    if (!root || root.dataset.favoritesReady === 'true') return;
    root.dataset.favoritesReady = 'true';

    var endpoint = root.dataset.favoritesUrl;
    var csrf = root.dataset.csrfToken || csrfToken();
    var favorites = initialFavorites();
    var favoriteSet = new Set(favorites);
    var pending = new Set();
    var navs = Array.prototype.slice.call(root.querySelectorAll('nav.sidebar-favorites-nav'));
    var records = [];

    navs.forEach(function (nav) {
      Array.prototype.slice.call(nav.querySelectorAll('a[href]')).forEach(function (anchor) {
        var path = pathFor(anchor);
        var label = labelFor(anchor);
        if (!path || !label || anchor.closest('.sidebar-favorites-panel')) return;
        records.push({ nav: nav, anchor: anchor, path: path, label: label });
      });
    });

    function showError(nav, message) {
      var status = nav.querySelector('.sidebar-favorites-status');
      if (!status) return;
      status.textContent = message;
      window.setTimeout(function () {
        if (status.textContent === message) status.textContent = '';
      }, 3500);
    }

    function updateStars() {
      records.forEach(function (record) {
        if (!record.toggle) return;
        var active = favoriteSet.has(record.path);
        record.toggle.textContent = active ? '\u2605' : '\u2606';
        record.toggle.classList.toggle('is-favorite', active);
        if (record.mobileReadonly) {
          record.toggle.removeAttribute('role');
          record.toggle.removeAttribute('tabindex');
          record.toggle.removeAttribute('aria-pressed');
          record.toggle.setAttribute('aria-hidden', 'true');
          record.toggle.title = active ? 'Favorito' : '';
          return;
        }
        record.toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
        record.toggle.setAttribute(
          'aria-label',
          (active ? 'Remover dos favoritos: ' : 'Adicionar aos favoritos: ') + record.label
        );
        record.toggle.title = active ? 'Remover dos favoritos' : 'Adicionar aos favoritos';
      });
    }

    function toggleFavorite(path, nav) {
      if (pending.has(path)) return;
      var wasFavorite = favoriteSet.has(path);
      var shouldFavorite = !wasFavorite;
      var failureMessage = '';
      pending.add(path);
      if (shouldFavorite) {
        favoriteSet.add(path);
        favorites.push(path);
      } else {
        favoriteSet.delete(path);
        favorites = favorites.filter(function (item) { return item !== path; });
      }
      updateStars();
      renderPanels();

      window.fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': csrf
        },
        body: JSON.stringify({ caminho: path, favorito: shouldFavorite })
      }).then(readJsonResponse).then(function (result) {
        if (!result.response.ok || !result.data.ok) {
          throw new Error(result.data.erro || 'Nao foi possivel salvar.');
        }
        favorites = result.data.favoritos;
        favoriteSet = new Set(favorites);
      }).catch(function (error) {
        if (wasFavorite) favoriteSet.add(path); else favoriteSet.delete(path);
        favorites = favorites.filter(function (item) { return item !== path; });
        if (wasFavorite) favorites.push(path);
        failureMessage = error.message || 'Nao foi possivel salvar o favorito.';
      }).finally(function () {
        pending.delete(path);
        updateStars();
        renderPanels();
        if (failureMessage) showError(nav, failureMessage);
      });
    }

    records.forEach(function (record) {
      record.anchor.classList.add('sidebar-favoritable-link');
      var toggle = document.createElement('span');
      toggle.className = 'sidebar-favorite-toggle';
      record.mobileReadonly = Boolean(record.nav.closest('.sidebar-mobile'));
      if (!record.mobileReadonly) {
        toggle.setAttribute('role', 'button');
        toggle.setAttribute('tabindex', '0');
        toggle.addEventListener('click', function (event) {
          event.preventDefault();
          event.stopPropagation();
          toggleFavorite(record.path, record.nav);
        });
        toggle.addEventListener('keydown', function (event) {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          event.stopPropagation();
          toggleFavorite(record.path, record.nav);
        });
      }
      record.anchor.appendChild(toggle);
      record.toggle = toggle;
    });

    function renderPanels() {
      navs.forEach(function (nav) {
        var oldPanel = nav.querySelector('.sidebar-favorites-panel');
        if (oldPanel) oldPanel.remove();

        var available = new Map();
        records.forEach(function (record) {
          if (record.nav === nav && !available.has(record.path)) available.set(record.path, record);
        });
        var visible = favorites.map(function (path) { return available.get(path); }).filter(Boolean);
        if (!visible.length) return;

        var panel = document.createElement('section');
        panel.className = 'sidebar-favorites-panel';
        panel.setAttribute('aria-label', 'Telas favoritas');

        var heading = document.createElement('div');
        heading.className = 'sidebar-favorites-heading';
        heading.innerHTML = '<span aria-hidden="true">\u2605</span><span>Favoritos</span>';
        panel.appendChild(heading);

        var list = document.createElement('div');
        list.className = 'sidebar-favorites-list';
        visible.forEach(function (record) {
          var row = document.createElement('div');
          row.className = 'sidebar-favorite-row';

          var link = document.createElement('a');
          link.className = 'sidebar-favorite-link';
          link.href = record.anchor.href;
          link.textContent = record.label;
          link.title = record.label;
          row.appendChild(link);

          var remove = document.createElement('span');
          remove.className = 'sidebar-favorite-toggle is-favorite';
          remove.textContent = '\u2605';
          if (nav.closest('.sidebar-mobile')) {
            remove.setAttribute('aria-hidden', 'true');
            remove.title = 'Favorito';
          } else {
            remove.setAttribute('role', 'button');
            remove.setAttribute('tabindex', '0');
            remove.setAttribute('aria-label', 'Remover dos favoritos: ' + record.label);
            remove.title = 'Remover dos favoritos';
            remove.addEventListener('click', function (event) {
              event.preventDefault();
              toggleFavorite(record.path, nav);
            });
            remove.addEventListener('keydown', function (event) {
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              toggleFavorite(record.path, nav);
            });
          }
          row.appendChild(remove);
          list.appendChild(row);
        });
        panel.appendChild(list);

        var status = document.createElement('div');
        status.className = 'sidebar-favorites-status';
        status.setAttribute('aria-live', 'polite');
        panel.appendChild(status);

        var logo = nav.querySelector('.sidebar-branch-logo-card');
        if (logo) logo.insertAdjacentElement('afterend', panel); else nav.prepend(panel);
      });
    }

    updateStars();
    renderPanels();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
