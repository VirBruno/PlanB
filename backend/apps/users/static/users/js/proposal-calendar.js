/* Selector personal: sólo actualiza el control de fecha, nunca la hora. */
(() => {
  const opener = document.querySelector('[data-open-proposal-calendar]');
  const dialog = document.querySelector('[data-proposal-calendar]');
  const dateInput = document.querySelector('[data-proposal-date]');
  if (!opener || !dialog || !dateInput || typeof dialog.showModal !== 'function') return;

  const content = dialog.querySelector('[data-calendar-content]');
  const status = dialog.querySelector('[data-calendar-status]');
  const retry = dialog.querySelector('[data-calendar-retry]');
  let controller;
  let requestId = 0;
  let lastMonth = '';

  async function loadMonth(month = '') {
    controller?.abort();
    controller = new AbortController();
    const currentRequest = ++requestId;
    lastMonth = month;
    const url = new URL(dialog.dataset.calendarUrl, window.location.origin);
    if (/^\d{4}-\d{2}$/.test(month)) {
      const [year, number] = month.split('-');
      url.searchParams.set('year', year);
      url.searchParams.set('month', number);
    }
    if (dateInput.value) url.searchParams.set('selected', dateInput.value);
    content.replaceChildren();
    content.setAttribute('aria-busy', 'true');
    retry.hidden = true;
    status.textContent = 'Cargando tu calendario…';
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', mode: 'same-origin', cache: 'no-store',
        signal: controller.signal,
      });
      if (response.redirected) throw new Error('session');
      if (!response.ok) throw new Error('calendar');
      const html = await response.text();
      if (currentRequest !== requestId || !dialog.open) return;
      content.innerHTML = html;
      status.textContent = '';
      const focusTarget = content.querySelector('[aria-pressed="true"]')
        || content.querySelector('.is-today[data-calendar-select]')
        || content.querySelector('[data-calendar-select]:not(.is-outside)');
      focusTarget?.focus({ preventScroll: true });
    } catch (error) {
      if (error.name === 'AbortError' || currentRequest !== requestId) return;
      status.textContent = error.message === 'session'
        ? 'Tu sesión venció. Volvé a iniciar sesión para consultar tus encuentros.'
        : 'No pudimos cargar tus encuentros. Reintentá o completá la fecha manualmente.';
      retry.hidden = false;
      retry.focus();
    } finally {
      if (currentRequest === requestId) content.removeAttribute('aria-busy');
    }
  }

  opener.hidden = false;
  opener.addEventListener('click', () => {
    dialog.showModal();
    document.documentElement.classList.add('proposal-calendar-open');
    loadMonth(dateInput.value.slice(0, 7));
  });
  retry.addEventListener('click', () => loadMonth(lastMonth));
  dialog.addEventListener('click', (event) => {
    if (event.target.closest('[data-close-proposal-calendar]')) {
      dialog.close();
      return;
    }
    const day = event.target.closest('[data-calendar-select]');
    if (day) {
      dateInput.value = day.dataset.calendarSelect;
      dateInput.dispatchEvent(new Event('input', { bubbles: true }));
      dateInput.dispatchEvent(new Event('change', { bubbles: true }));
      dialog.close();
      return;
    }
    const navigation = event.target.closest('[data-calendar-month]');
    if (navigation) loadMonth(navigation.dataset.calendarMonth);
    if (event.target === dialog) {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    }
  });
  dialog.addEventListener('keydown', (event) => {
    const day = event.target.closest('[data-calendar-select]');
    const offset = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (!day || offset === undefined) return;
    const days = [...content.querySelectorAll('[data-calendar-select]')];
    const next = days[days.indexOf(day) + offset];
    if (next) { event.preventDefault(); next.focus(); }
  });
  // Escape usa el comportamiento nativo de <dialog>, con foco y modal accesibles.
  dialog.addEventListener('close', () => {
    if (dialog.open) return; // Un cierre anterior puede llegar después de reabrir.
    ++requestId;
    controller?.abort();
    content.removeAttribute('aria-busy');
    document.documentElement.classList.remove('proposal-calendar-open');
    opener.focus();
  });
})();
