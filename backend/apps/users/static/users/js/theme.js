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
    if (button.getAttribute('role') === 'switch') button.setAttribute('aria-checked', dark ? 'true' : 'false');
    // PLANB_SWITCH_KNOB_FIX_03: actualizar el ícono sin borrar el elemento que se desliza.
    if (button.getAttribute('role') === 'switch') {
      var knob = button.querySelector('.pb-theme-sidebar-knob');
      if (knob) knob.textContent = dark ? '☀' : '☾';
    } else {
      button.textContent = compact ? (dark ? '☀' : '☾') : (dark ? '☀ Modo claro' : '☾ Modo oscuro');
    }
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
