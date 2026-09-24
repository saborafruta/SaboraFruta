(function () {
  'use strict';
  var root = document.querySelector('.inicio-page');
  if (!root) return;
  var csrf = document.querySelector('meta[name="erp-csrf-token"]');

  function relativeTime(value) {
    var seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 60) return 'Agora há pouco';
    var minutes = Math.floor(seconds / 60);
    if (minutes < 60) return 'Há ' + minutes + ' min';
    var hours = Math.floor(minutes / 60);
    if (hours < 24) return 'Há ' + hours + ' h';
    var days = Math.floor(hours / 24);
    return 'Há ' + days + (days === 1 ? ' dia' : ' dias');
  }

  function renderRecent() {
    var target = document.getElementById('recent-pages');
    var pages = window.ERPRecentPages ? window.ERPRecentPages.read() : [];
    if (!pages.length) return;
    target.innerHTML = '';
    pages.slice(0, 5).forEach(function (page, index) {
      var link = document.createElement('a');
      link.className = 'recent-page';
      link.href = page.path;
      var number = document.createElement('span');
      number.className = 'recent-page-index';
      number.textContent = String(index + 1).padStart(2, '0');
      var copy = document.createElement('span');
      copy.className = 'recent-page-copy';
      var strong = document.createElement('strong');
      strong.textContent = page.title;
      var small = document.createElement('small');
      small.textContent = relativeTime(page.visitedAt);
      copy.appendChild(strong); copy.appendChild(small);
      link.appendChild(number); link.appendChild(copy);
      link.insertAdjacentHTML('beforeend', '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.7" d="m9 18 6-6-6-6"/></svg>');
      target.appendChild(link);
    });
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'X-CSRFToken': csrf ? csrf.content : '', 'Content-Type': 'application/json', 'Accept': 'application/json'},
      body: JSON.stringify(payload || {})
    }).then(function (response) {
      if (!response.ok) throw new Error('Não foi possível salvar a alteração.');
      return response.json();
    });
  }

  function emptyNotificationsIfNeeded() {
    var list = document.getElementById('home-notifications');
    if (list.querySelector('.notification-row')) return;
    list.innerHTML = '<div class="inicio-empty notifications-empty"><span class="empty-check" aria-hidden="true"><svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M5 13l4 4L19 7"/></svg></span><div><strong>Tudo em dia</strong><p>Você não tem notificações pendentes.</p></div></div>';
    var all = root.querySelector('.mark-all'); if (all) all.remove();
  }

  root.querySelectorAll('.notification-check').forEach(function (button) {
    button.addEventListener('click', function () {
      var row = button.closest('.notification-row');
      button.disabled = true;
      row.classList.add('is-acknowledging');
      post(button.dataset.url).then(function () {
        window.setTimeout(function () {
          row.remove();
          emptyNotificationsIfNeeded();
        }, 650);
      }).catch(function () {
        row.classList.remove('is-acknowledging');
        button.disabled = false;
      });
    });
  });
  var markAll = root.querySelector('.mark-all');
  if (markAll) markAll.addEventListener('click', function () {
    markAll.disabled = true;
    post(markAll.dataset.url).then(function () {
      root.querySelectorAll('.notification-row').forEach(function (row) { row.remove(); });
      emptyNotificationsIfNeeded();
    }).catch(function () { markAll.disabled = false; });
  });

  var dialog = document.getElementById('shortcut-dialog');
  function openDialog() { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); }
  document.getElementById('open-shortcuts').addEventListener('click', openDialog);
  document.getElementById('add-shortcut').addEventListener('click', openDialog);
  document.getElementById('close-shortcuts').addEventListener('click', function () { dialog.close(); });
  document.getElementById('finish-shortcuts').addEventListener('click', function () { window.location.reload(); });
  dialog.addEventListener('click', function (event) { if (event.target === dialog) dialog.close(); });
  document.getElementById('shortcut-search').addEventListener('input', function (event) {
    var query = event.target.value.trim().toLowerCase();
    root.querySelectorAll('.shortcut-option').forEach(function (option) {
      option.hidden = query && option.dataset.name.indexOf(query) === -1;
    });
  });
  root.querySelectorAll('.shortcut-option').forEach(function (option) {
    option.addEventListener('click', function () {
      var selected = !option.classList.contains('is-selected');
      option.disabled = true;
      post(root.dataset.favoritesUrl, {caminho: option.dataset.path, favorito: selected}).then(function () {
        option.classList.toggle('is-selected', selected);
        option.setAttribute('aria-pressed', selected ? 'true' : 'false');
      }).finally(function () { option.disabled = false; });
    });
  });
  renderRecent();
}());
