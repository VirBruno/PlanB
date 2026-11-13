(() => {
  const selector = document.querySelector('[data-uno-mode]');
  if (!selector) return;
  const choices = [...selector.querySelectorAll('[data-uno-mode-choice]')];
  const panels = [...document.querySelectorAll('[data-uno-mode-panel]')];

  function selectMode(mode, updateUrl = true) {
    const choice = choices.find(item => item.dataset.unoModeChoice === mode && !item.disabled);
    const selected = choice && panels.some(panel => panel.dataset.unoModePanel === mode) ? mode : null;
    for (const item of choices) item.setAttribute('aria-expanded', String(item.dataset.unoModeChoice === selected));
    for (const panel of panels) panel.hidden = panel.dataset.unoModePanel !== selected;
    if (updateUrl) {
      const url = new URL(window.location.href);
      if (selected) url.searchParams.set('uno_mode', selected);
      else url.searchParams.delete('uno_mode');
      window.history.replaceState(window.history.state, '', url);
    }
    document.dispatchEvent(new CustomEvent('uno-mode-change', { detail: { mode: selected } }));
  }

  choices.forEach(choice => choice.addEventListener('click', () => {
    selectMode(choice.getAttribute('aria-expanded') === 'true' ? null : choice.dataset.unoModeChoice);
  }));
  window.addEventListener('popstate', () => selectMode(new URL(window.location.href).searchParams.get('uno_mode'), false));
  selectMode(new URL(window.location.href).searchParams.get('uno_mode'), false);
})();
