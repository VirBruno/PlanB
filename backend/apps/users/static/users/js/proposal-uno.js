(() => {
  const root = document.querySelector('[data-uno-game]');
  if (!root) return;
  const get = name => root.querySelector(`[data-uno-${name}]`);
  const labels = { salto: 'Salto', reversa: 'Reversa', mas_dos: '+2', comodin: 'Comodín', mas_cuatro: '+4' };
  const colors = { rojo: 'Rojo', amarillo: 'Amarillo', verde: 'Verde', azul: 'Azul' };
  const colorCodes = { rojo: 'R', amarillo: 'Y', verde: 'G', azul: 'B' };
  const valueCodes = { salto: 'S', reversa: 'R', mas_dos: 'A2' };
  let game = null;
  let busy = false;
  let selectedCard = null;
  let playbackVersion = 0;

  async function slideToTable(source, card, hideTarget = false) {
    const target = get('top').firstElementChild;
    if (!source || !target || !source.animate ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const from = source.getBoundingClientRect();
    const to = target.getBoundingClientRect();
    if (!from.width || !from.height || !to.width || !to.height) return;

    const moving = cardElement(card);
    moving.classList.add('proposal-uno__moving-card');
    moving.setAttribute('aria-hidden', 'true');
    Object.assign(moving.style, {
      position: 'fixed', left: `${from.left}px`, top: `${from.top}px`,
      width: `${from.width}px`, height: `${from.height}px`, margin: '0',
      zIndex: '1000', pointerEvents: 'none', transformOrigin: 'top left'
    });
    root.appendChild(moving);
    if (hideTarget) target.style.visibility = 'hidden';
    try {
      const animation = moving.animate([
        { transform: 'translate(0, 0) scale(1, 1)' },
        { transform: `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${to.width / from.width}, ${to.height / from.height})` }
      ], { duration: 380, easing: 'ease-in-out', fill: 'forwards' });
      await animation.finished;
    } catch (_) {
      // La partida sigue funcionando aunque el navegador interrumpa la animaci?n.
    } finally {
      moving.remove();
      if (hideTarget) target.style.visibility = '';
    }
  }

  async function showEvents(events, sources, version) {
    for (const event of events || []) {
      if (version !== playbackVersion) return;
      if (event.type === 'play') {
        await slideToTable(sources[event.actor], event);
        if (version !== playbackVersion) return;
      }
      let title = '';
      let detail = '';
      let playedCard = null;
      if (event.type === 'uno') {
        title = '¡UNO!';
        detail = event.actor === 'player' ? 'Te queda una carta' : 'Al bot le queda una carta';
      } else if (event.type === 'draw' && event.reason !== 'deck') {
        title = event.reason === 'mas_dos' ? '¡+2!' : '¡+4!';
        const who = event.actor === 'player' ? 'Robás' : 'El bot roba';
        detail = `${who} ${event.count} ${event.count === 1 ? 'carta' : 'cartas'}`;
      } else if (event.type === 'draw' && event.count > 0) {
        title = event.actor === 'player' ? 'Robaste' : 'El bot robó';
        detail = `${event.count} ${event.count === 1 ? 'carta' : 'cartas'} del mazo`;
      } else if (event.type === 'play' && event.actor === 'bot') {
        title = 'Jugó el bot';
        detail = labels[event.value] || event.value;
        playedCard = cardElement({ value: event.value, color: event.color });
      }
      if (!title) continue;
      get('flash-title').textContent = title;
      get('flash-detail').textContent = detail;
      get('flash-card').replaceChildren(...(playedCard ? [playedCard] : []));
      get('flash').hidden = false;
      await new Promise(resolve => setTimeout(resolve, playedCard ? 850 : 600));
      if (version !== playbackVersion) return;
      get('flash').hidden = true;
    }
  }

  function cardElement(card, interactive = false) {
    const element = document.createElement(interactive ? 'button' : 'div');
    const value = labels[card.value] || card.value;
    element.className = 'proposal-uno__card';
    const description = `${value}${card.color ? ` ${colors[card.color]}` : ''}`;
    element.setAttribute('aria-label', description);
    if (interactive) {
      element.type = 'button';
      element.dataset.unoCardId = card.id;
      element.disabled = busy || !card.playable;
      element.addEventListener('click', () => {
        if (!card.color) {
          selectedCard = card.id;
          get('colors').hidden = false;
          get('colors').querySelector('button').focus();
        } else {
          send('play', { card_id: card.id });
        }
      });
    }
    const code = card.value === 'comodin' ? 'WC' : card.value === 'mas_cuatro' ? 'W4'
      : `${colorCodes[card.color]}${valueCodes[card.value] || card.value}`;
    const image = document.createElement('img');
    image.src = `${root.dataset.unoCardBase}${code}.${code === 'R0' || code === 'Y1' ? 'png' : 'gif'}`;
    image.alt = '';
    image.draggable = false;
    element.append(image);
    return element;
  }

  function render() {
    root.dataset.unoBusy = String(busy);
    get('start-panel').hidden = Boolean(game);
    get('board').hidden = !game;
    get('start').disabled = busy;
    if (!game) return;
    get('bot-count').textContent = `${game.bot_count} ${game.bot_count === 1 ? 'carta' : 'cartas'}`;
    const backs = Array.from({ length: game.bot_count }, () => {
      const back = document.createElement('span');
      back.className = 'proposal-truco-card-back';
      return back;
    });
    get('bot-cards').replaceChildren(...backs);
    get('top').replaceChildren(cardElement(game.top));
    get('color').textContent = colors[game.color];
    get('color').dataset.color = game.color;
    get('hand').replaceChildren(...game.hand.map(card => cardElement(card, true)));
    get('draw').disabled = busy || game.turn !== 'player' || game.drawn || Boolean(game.winner);
    get('pass').hidden = !game.drawn || Boolean(game.winner);
    get('pass').disabled = busy;
    get('restart').hidden = !game.winner;
    get('restart').disabled = busy;
    get('notices').replaceChildren(...(game.announcements || []).map(message => {
      const notice = document.createElement('span');
      notice.textContent = message;
      return notice;
    }));
    get('message').textContent = game.message;
  }

  async function send(action, params = {}) {
    if (busy) return;
    const version = ++playbackVersion;
    get('flash').hidden = true;
    root.querySelectorAll('.proposal-uno__moving-card').forEach(card => {
      card.getAnimations().forEach(animation => animation.cancel());
    });
    busy = true;
    selectedCard = null;
    get('colors').hidden = true;
    render();
    if (action === 'draw' && game) {
      // The server decides which card was drawn; show its back while it responds.
      const pending = get('draw').cloneNode(true);
      pending.removeAttribute('data-uno-draw');
      pending.setAttribute('aria-label', 'Robando una carta');
      pending.disabled = true;
      get('hand').append(pending);
      get('message').textContent = 'Robando una carta?';
    }
    try {
      const options = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
      if (action) {
        options.method = 'POST';
        options.headers['X-CSRFToken'] = root.querySelector('[name=csrfmiddlewaretoken]').value;
        options.body = new URLSearchParams({ action, ...params });
      }
      const response = await fetch(root.dataset.unoUrl, options);
      const data = await response.json();
      if (!response.ok) {
        if (data.game) game = data.game;
        throw new Error(data.error || 'No pudimos continuar la partida.');
      }
      // Preserve the origin positions before rendering the server's new state.
      const snapshot = element => {
        if (!element) return null;
        const rect = element.getBoundingClientRect();
        return { animate: true, getBoundingClientRect: () => rect };
      };
      const sources = {
        player: snapshot(Array.from(get('hand').children).find(card => card.dataset.unoCardId === String(params.card_id))),
        bot: snapshot(get('bot-cards').lastElementChild)
      };
      game = data.game;
      const victories = document.querySelector('[data-uno-victories]');
      if (victories && Number.isInteger(data.victories)) {
        victories.textContent = `${data.victories} ${data.victories === 1 ? 'victoria' : 'victorias'}`;
      }
      const refresh = get('results-refresh');
      if (refresh) refresh.hidden = !game?.winner;
      busy = false;
      render();
      if (action === 'start' && game) {
        slideToTable(get('draw'), game.top).catch(() => {});
      } else if (action && game) {
        showEvents(game.events, sources, version).catch(() => {
          if (version === playbackVersion) get('flash').hidden = true;
        });
      }
      if (!game) get('message').textContent = 'Todo listo para jugar.';
    } catch (error) {
      busy = false;
      render();
      get('message').textContent = error.message || 'No pudimos conectar. Probá de nuevo.';
    }
  }

  get('start').addEventListener('click', () => send('start'));
  get('restart').addEventListener('click', () => send('start'));
  get('draw').addEventListener('click', () => send('draw'));
  get('pass').addEventListener('click', () => send('pass'));
  get('cancel').addEventListener('click', () => { selectedCard = null; get('colors').hidden = true; });
  root.querySelectorAll('[data-uno-choose]').forEach(button => button.addEventListener('click', () => {
    if (selectedCard) send('play', { card_id: selectedCard, color: button.dataset.unoChoose });
  }));
  send();
})();
