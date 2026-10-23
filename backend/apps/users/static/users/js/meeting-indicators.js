/* Plan B - Etapa 7: estado temporal del encuentro sin modificar datos ni consultar APIs. */
(function () {
  'use strict';

  const today = new Date();
  const DAY_MS = 24 * 60 * 60 * 1000;
  const dayNumber = date => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / DAY_MS;

  function statusFor(iso) {
    if (!iso) return null;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return null;
    if (date.getTime() < today.getTime()) {
      return { label: 'Ya pasó', type: 'past' };
    }
    const days = Math.round(dayNumber(date) - dayNumber(today));
    if (days === 0) return { label: 'Hoy', type: 'today' };
    if (days === 1) return { label: 'Mañana', type: 'soon' };
    if (days <= 7) return { label: `En ${days} días`, type: 'soon' };
    return { label: 'Próximamente', type: 'future' };
  }

  function addBadge(card, iso, targetSelector) {
    const status = statusFor(iso);
    if (!status || card.querySelector('[data-meeting-badge]')) return;
    const target = card.querySelector(targetSelector);
    if (!target) return;
    const badge = document.createElement('span');
    badge.className = `pb-meeting-status pb-meeting-status--${status.type}`;
    badge.dataset.meetingBadge = '';
    badge.textContent = status.label;
    badge.setAttribute('aria-label', `Encuentro: ${status.label}`);
    target.appendChild(badge);
  }

  document.querySelectorAll('li[data-plan-scheduled]').forEach(item => {
    const card = item.querySelector('.pb-plan-card');
    if (card) addBadge(card, item.dataset.planScheduled, '.pb-plan-card-end');
  });
  document.querySelectorAll('a.group-social-plan-card[data-meeting-at]').forEach(card => {
    addBadge(card, card.dataset.meetingAt, '.group-social-plan-info');
  });
}());
