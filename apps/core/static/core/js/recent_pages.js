(function () {
  'use strict';

  var userMeta = document.querySelector('meta[name="erp-table-user-id"]');
  var branchMeta = document.querySelector('meta[name="erp-active-branch-id"]');
  var homeMeta = document.querySelector('meta[name="erp-home-url"]');
  if (!userMeta || !homeMeta) return;

  var key = 'erp-recent-pages:' + userMeta.content + ':' + (branchMeta ? branchMeta.content : '');
  var homePath = homeMeta.content || '/inicio/';

  function read() {
    try {
      var value = JSON.parse(localStorage.getItem(key) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (error) {
      return [];
    }
  }

  function title() {
    var heading = document.querySelector('main h1, main h2, [data-page-title]');
    var value = heading && heading.textContent.trim();
    if (!value) value = (document.title || '').split('—')[0].trim();
    return value || 'Tela do sistema';
  }

  function record() {
    var path = window.location.pathname;
    if (!path || path === homePath || path.indexOf('/auth/') === 0 || path.indexOf('/notificacoes/') === 0) return;
    var pages = read().filter(function (item) { return item.path !== path; });
    pages.unshift({path: path, title: title(), visitedAt: new Date().toISOString()});
    try { localStorage.setItem(key, JSON.stringify(pages.slice(0, 8))); } catch (error) {}
  }

  window.ERPRecentPages = {read: read, record: record};
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', record);
  } else {
    record();
  }
}());
