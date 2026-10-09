(function () {
  'use strict';
  var root = document.documentElement;
  var storeKey = 'planb-theme';
  var button = document.querySelector('[data-theme-toggle]');
  if (!button) return;
  var compact = button.hasAttribute('data-theme-compact');
  function render() {
    var dark = root.dataset.theme === 'dark';
    button.setAttribute('aria-label', dark ? 'Activar modo claro' : 'Activar modo oscuro');
    button.setAttribute('aria-pressed', dark ? 'true' : 'false');
    if (!compact) button.textContent = dark ? '☀ Modo claro' : '☾ Modo oscuro';
    button.title = dark ? 'Activar modo claro' : 'Activar modo oscuro';
  }
  button.addEventListener('click', function () {
    var next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem(storeKey, next); } catch (error) { /* No persistent storage */ }
    render();
  });
  render();
}());
