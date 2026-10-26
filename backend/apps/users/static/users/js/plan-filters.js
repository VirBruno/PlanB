/* Plan B · Etapa 14: organizar, buscar y ordenar planes cargados en la página.
   No cambia datos, permisos ni paginación; funciona sin servicios adicionales. */
(function () {
  'use strict';
  const toolbar = document.querySelector('[data-plan-filters]');
  if (!toolbar) return;
  const sections = Array.from(document.querySelectorAll('[data-plan-section]'));
  const items = sections.flatMap(section => Array.from(section.querySelectorAll(':scope > [data-plan-list] > li[data-plan-state]')));
  if (!items.length) return;

  const buttons = Array.from(toolbar.querySelectorAll('[data-plan-filter]'));
  const search = toolbar.querySelector('[data-plan-search]');
  const sort = toolbar.querySelector('[data-plan-sort]');
  const empty = document.querySelector('[data-plan-filter-empty]');
  const reset = document.querySelector('[data-plan-reset]');
  const announcement = document.querySelector('[data-plan-filter-announcement]');
  const counts = {
    all: items.length,
    active: items.filter(item => item.dataset.planState === 'active').length,
    inactive: items.filter(item => item.dataset.planState === 'inactive').length,
    organizing: items.filter(item => item.dataset.planPhase === 'organizing').length,
    confirmed: items.filter(item => item.dataset.planPhase === 'confirmed').length,
    finished: items.filter(item => item.dataset.planPhase === 'finished').length,
    upcoming: items.filter(item => {
      const t = Date.parse(item.dataset.planScheduled || '');
      return item.dataset.planState === 'active' && Number.isFinite(t) && t > Date.now();
    }).length
  };
  let filter = 'all';
  const entries = items.map((item, originalIndex) => ({
    item,
    originalIndex,
    created: Date.parse(item.dataset.planCreated || '') || 0,
    scheduled: Date.parse(item.dataset.planScheduled || ''),
    title: (item.querySelector('.pb-plan-info strong')?.textContent || '').trim(),
    content: normalize(item.querySelector('.pb-plan-info')?.textContent || '')
  }));

  function normalize(value) {
    return String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
  }
  function setText(selector, value) {
    document.querySelectorAll(selector).forEach(element => { element.textContent = String(value); });
  }
  Object.keys(counts).forEach(key => setText(`[data-plan-count="${key}"]`, counts[key]));
  setText('[data-plan-overview-count="active"]', counts.active);
  setText('[data-plan-overview-count="inactive"]', counts.inactive);
  setText('[data-plan-overview-count="upcoming"]', counts.upcoming);

  function update() {
    const term = normalize(search?.value || '');
    const ordering = sort?.value || 'recent';
    const visibleCounts = { active: 0, inactive: 0 };

    entries.forEach(entry => {
      // ETAPA9_PHASES: la fase distingue propuesta confirmada del status técnico.
      const state = entry.item.dataset.planState;
      const phase = entry.item.dataset.planPhase;
      const isUpcoming = state === 'active' && Number.isFinite(entry.scheduled) && entry.scheduled > Date.now();
      const show = (filter === 'all' || filter === state || filter === phase || (filter === 'upcoming' && isUpcoming)) && (!term || entry.content.includes(term));
      entry.item.hidden = !show;
      if (show) visibleCounts[state]++;
    });
    sections.forEach(section => {
      const state = section.dataset.planSection;
      const list = section.querySelector('[data-plan-list]');
      const stateEntries = entries.filter(entry => entry.item.dataset.planState === state);
      stateEntries.sort((a, b) => {
        if (ordering === 'upcoming') {
          const now = Date.now();
          const bucket = e => !Number.isFinite(e.scheduled) ? 2 : (e.scheduled >= now ? 0 : 1);
          const aBucket = bucket(a);
          const bBucket = bucket(b);
          if (aBucket !== bBucket) return aBucket - bBucket;
          // Próximos primero; los ya pasados después; sin fecha al final.
          if (aBucket === 0) return a.scheduled - b.scheduled || b.created - a.created;
          if (aBucket === 1) return b.scheduled - a.scheduled || b.created - a.created;
          return b.created - a.created || a.originalIndex - b.originalIndex;
        }
        if (ordering === 'name') return a.title.localeCompare(b.title, 'es', { sensitivity: 'base' }) || a.originalIndex - b.originalIndex;
        if (ordering === 'oldest') return a.created - b.created || a.originalIndex - b.originalIndex;
        return b.created - a.created || a.originalIndex - b.originalIndex;
      });
      // Reordenar nodos existentes preserva sus enlaces y no crea nuevos elementos.
      stateEntries.forEach(entry => list.appendChild(entry.item));
      section.hidden = visibleCounts[state] === 0;
      const counter = section.querySelector('[data-plan-visible-count]');
      if (counter) counter.textContent = String(visibleCounts[state]);
    });
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.planFilter === filter)));
    const total = visibleCounts.active + visibleCounts.inactive;
    if (empty) empty.hidden = total !== 0;
    if (announcement) announcement.textContent = `${total} ${total === 1 ? 'plan visible' : 'planes visibles'} en esta página.`;
  }

  buttons.forEach(button => button.addEventListener('click', () => {
    filter = button.dataset.planFilter;
    update();
  }));
  search?.addEventListener('input', update);
  sort?.addEventListener('change', update);
  reset?.addEventListener('click', () => {
    filter = 'all';
    if (search) search.value = '';
    if (sort) sort.value = 'recent';
    update();
    search?.focus();
  });
  toolbar.hidden = false;
  update();
}());
