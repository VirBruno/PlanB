(() => {
  const root = document.querySelector('[data-uno-duel]');
  if (!root) return;
  const get = name => root.querySelector(`[data-duel-${name}]`);
  const colors = { rojo: 'Rojo', amarillo: 'Amarillo', verde: 'Verde', azul: 'Azul' };
  const colorCodes = { rojo: 'R', amarillo: 'Y', verde: 'G', azul: 'B' };
  const values = { salto: 'Salto', reversa: 'Reversa', mas_dos: '+2', comodin: 'Comodín', mas_cuatro: '+4' };
  const valueCodes = { salto: 'S', reversa: 'R', mas_dos: 'A2' };
  const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
  let game = null;
  let matchId = null;
  let opponent = 'Rival';
  let busy = false;
  let timer = null;
  let selectedCard = null;
  let playback = 0;
  let lastLobby = '';
  let pollController = null;
  let pendingAction = null;

  function gameUrl(id) {
    return root.dataset.duelGameUrl.replace('00000000-0000-0000-0000-000000000000', id);
  }

  async function request(url, params, signal) {
    const options = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (signal) options.signal = signal;
    if (params) {
      options.method = 'POST';
      options.headers['X-CSRFToken'] = csrf;
      options.body = new URLSearchParams(params);
    }
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'No pudimos continuar. Probá de nuevo.');
    return data;
  }

  function message(text) { get('message').textContent = text; }
  function schedule() {
    clearTimeout(timer);
    if (root.hidden) return;
    if (matchId && game?.winner) return;
    timer = setTimeout(() => {
      if (document.hidden) { schedule(); return; }
      if (matchId) loadGame();
      else loadLobby();
    }, matchId ? 2000 : 5000);
  }

  function button(label, callback, primary = false) {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = primary ? 'proposal-uno__button' : 'proposal-uno-duel__secondary';
    element.textContent = label;
    element.addEventListener('click', callback);
    return element;
  }

  function renderLobby(data) {
    const signature = JSON.stringify(data);
    if (signature === lastLobby) return;
    lastLobby = signature;
    const selectedMember = get('members').value;
    get('members').replaceChildren(new Option('Elegí un integrante', ''));
    data.members.forEach(member => get('members').add(new Option(member.username, member.id)));
    if (data.members.some(member => member.id === selectedMember)) get('members').value = selectedMember;
    const selected = new Map([...get('matches').querySelectorAll('[data-match-proposal]')]
      .map(select => [select.dataset.matchProposal, select.value]));
    get('matches').replaceChildren();
    if (!data.matches.length) {
      const empty = document.createElement('p');
      empty.className = 'proposal-uno-duel__empty';
      empty.textContent = 'Todavía no tenés invitaciones ni partidas.';
      get('matches').append(empty);
      return;
    }
    data.matches.forEach(match => {
      const item = document.createElement('div');
      item.className = 'proposal-uno-duel__match';
      const info = document.createElement('div');
      info.className = 'proposal-uno-duel__match-info';
      const title = document.createElement('strong');
      title.textContent = match.opponent_username;
      const detail = document.createElement('span');
      if (match.status === 'pending') detail.textContent = match.role === 'invitee' ? 'Te invitó a jugar UNO.' : 'Esperando respuesta.';
      else if (match.status === 'playing') detail.textContent = 'Partida en curso.';
      else if (match.status === 'finished') detail.textContent = match.winner === 'player' ? '¡Ganaste la partida!' : 'Ganó tu rival.';
      else detail.textContent = match.status === 'rejected' ? 'Invitación rechazada.' : 'Invitación cancelada.';
      info.append(title, detail);
      item.append(info);
      if (match.status === 'pending' && match.role === 'invitee') {
        const select = get('proposals').cloneNode(true);
        select.removeAttribute('data-duel-proposals');
        select.name = '';
        select.dataset.matchProposal = match.id;
        select.setAttribute('aria-label', `Propuesta que representás contra ${match.opponent_username}`);
        select.value = selected.get(match.id) || '';
        item.append(select, button('Aceptar', () => {
          if (!select.value) { message('Elegí la propuesta que querés representar.'); select.focus(); return; }
          lobbyAction({ action: 'accept', match_id: match.id, proposal_id: select.value }, match);
        }, true), button('Rechazar', () => lobbyAction({ action: 'reject', match_id: match.id })));
      } else if (match.status === 'playing' || match.status === 'finished') {
        item.append(button(match.status === 'playing' ? 'Entrar a la partida' : 'Ver resultado', () => openGame(match), true));
      }
      get('matches').append(item);
    });
  }

  async function loadLobby() {
    if (root.hidden) return;
    if (busy || matchId) { schedule(); return; }
    busy = true;
    const controller = new AbortController();
    pollController = controller;
    try {
      const data = await request(root.dataset.duelLobbyUrl, null, controller.signal);
      if (!matchId) renderLobby(data);
    } catch (error) { if (error.name !== 'AbortError') message(error.message); }
    finally {
      if (pollController === controller) pollController = null;
      busy = false;
      if (pendingAction) { const next = pendingAction; pendingAction = null; next(); }
      else schedule();
    }
  }

  async function lobbyAction(params, acceptedMatch = null) {
    if (busy) {
      if (pollController) { pendingAction = () => lobbyAction(params, acceptedMatch); pollController.abort(); }
      return;
    }
    busy = true;
    get('invite').querySelectorAll('button, select').forEach(el => { el.disabled = true; });
    try {
      await request(root.dataset.duelLobbyUrl, params);
      lastLobby = '';
      if (params.action === 'invite') { message('Invitación enviada.'); get('invite').reset(); }
      else if (params.action === 'reject') message('Invitación rechazada.');
      else message('¡A jugar!');
    } catch (error) { message(error.message); acceptedMatch = null; }
    finally {
      busy = false;
      get('invite').querySelectorAll('button, select').forEach(el => { el.disabled = false; });
    }
    if (acceptedMatch) openGame(acceptedMatch);
    else loadLobby();
  }

  function cardElement(card, interactive = false) {
    const element = document.createElement(interactive ? 'button' : 'div');
    element.className = 'proposal-uno__card';
    element.setAttribute('aria-label', `${values[card.value] || card.value}${card.color ? ` ${colors[card.color]}` : ''}`);
    if (interactive) {
      element.type = 'button';
      element.dataset.duelCardId = card.id;
      element.disabled = busy || !card.playable;
      element.addEventListener('click', () => {
        if (!card.color) {
          selectedCard = card.id;
          get('colors').hidden = false;
          get('colors').querySelector('button').focus();
        } else sendGame('play', { card_id: card.id });
      });
    }
    const code = card.value === 'comodin' ? 'WC' : card.value === 'mas_cuatro' ? 'W4'
      : `${colorCodes[card.color]}${valueCodes[card.value] || card.value}`;
    const img = document.createElement('img');
    img.src = `${root.dataset.duelCardBase}${code}.${code === 'R0' || code === 'Y1' ? 'png' : 'gif'}`;
    img.alt = '';
    img.draggable = false;
    element.append(img);
    return element;
  }

  function renderGame() {
    if (!game) return;
    get('opponent').textContent = opponent;
    get('opponent-count').textContent = `${game.opponent_count} ${game.opponent_count === 1 ? 'carta' : 'cartas'}`;
    get('opponent-cards').replaceChildren(...Array.from({ length: game.opponent_count }, () => {
      const back = document.createElement('span'); back.className = 'proposal-truco-card-back'; return back;
    }));
    get('top').replaceChildren(cardElement(game.top));
    get('color').textContent = colors[game.color];
    get('color').dataset.color = game.color;
    get('hand').replaceChildren(...game.hand.map(card => cardElement(card, true)));
    get('draw').disabled = busy || game.turn !== 'player' || game.drawn || Boolean(game.winner);
    get('pass').hidden = !game.drawn || Boolean(game.winner);
    get('pass').disabled = busy;
    get('forfeit').hidden = Boolean(game.winner);
    get('forfeit').disabled = busy;
    get('back').disabled = busy;
    get('results').hidden = !game.winner;
    get('turn').textContent = game.winner ? 'Partida terminada' : game.turn === 'player' ? 'Es tu turno' : `Turno de ${opponent}`;
    if (game.winner) message(game.winner === 'player' ? '¡Ganaste! Tu propuesta suma un punto.' : 'Ganó tu rival.');
  }

  async function slide(source, card) {
    const target = get('top').firstElementChild;
    if (!source || !target || !target.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const to = target.getBoundingClientRect();
    if (!source.width || !source.height || !to.width || !to.height) return;
    const moving = cardElement(card);
    moving.classList.add('proposal-uno__moving-card');
    moving.setAttribute('aria-hidden', 'true');
    Object.assign(moving.style, { position: 'fixed', left: `${source.left}px`, top: `${source.top}px`,
      width: `${source.width}px`, height: `${source.height}px`, margin: '0', zIndex: '1000',
      pointerEvents: 'none', transformOrigin: 'top left' });
    root.append(moving);
    try {
      await moving.animate([
        { transform: 'translate(0, 0) scale(1, 1)' },
        { transform: `translate(${to.left - source.left}px, ${to.top - source.top}px) scale(${to.width / source.width}, ${to.height / source.height})` }
      ], { duration: 380, easing: 'ease-in-out', fill: 'forwards' }).finished;
    } catch (_) { /* Una animación interrumpida no cambia la partida. */ }
    finally { moving.remove(); }
  }

  async function showEvents(events, sources, sequence) {
    for (const event of events) {
      if (sequence !== playback || !matchId) return;
      if (event.type === 'play') {
        await slide(sources[event.actor], event);
        if (sequence !== playback || !matchId) return;
      }
      let title = '', detail = '', image = null;
      if (event.type === 'uno') {
        title = '¡UNO!'; detail = event.actor === 'player' ? 'Te queda una carta' : `A ${opponent} le queda una carta`;
      } else if (event.type === 'draw') {
        title = event.reason === 'mas_dos' ? '¡+2!' : event.reason === 'mas_cuatro' ? '¡+4!' : '¡A robar!';
        detail = `${event.actor === 'player' ? 'Robás' : `${opponent} roba`} ${event.count} ${event.count === 1 ? 'carta' : 'cartas'}`;
      } else if (event.type === 'play' && event.actor === 'opponent') {
        title = `Jugó ${opponent}`; detail = values[event.value] || event.value; image = cardElement(event);
      } else if (event.type === 'forfeit') {
        title = event.actor === 'player' ? 'Abandonaste' : `${opponent} abandonó`;
      }
      if (!title) continue;
      get('flash-title').textContent = title;
      get('flash-detail').textContent = detail;
      get('flash-card').replaceChildren(...(image ? [image] : []));
      get('flash').hidden = false;
      await new Promise(resolve => setTimeout(resolve, image ? 850 : 600));
      if (sequence !== playback || !matchId) return;
      get('flash').hidden = true;
    }
  }

  function stopPlayback() {
    playback += 1;
    get('flash').hidden = true;
    root.querySelectorAll('.proposal-uno__moving-card').forEach(card => card.getAnimations().forEach(animation => animation.cancel()));
  }

  function openGame(match) {
    if (busy) return;
    matchId = match.id;
    opponent = match.opponent_username;
    game = null;
    get('lobby').hidden = true;
    get('board').hidden = false;
    get('hand').replaceChildren();
    get('top').replaceChildren();
    get('opponent-cards').replaceChildren();
    get('opponent-count').textContent = '';
    get('turn').textContent = '';
    get('results').hidden = true;
    get('draw').disabled = true;
    get('forfeit').disabled = true;
    get('pass').hidden = true;
    message('Preparando la mesa…');
    loadGame();
  }

  async function loadGame(action = null, params = {}) {
    if (!matchId || (root.hidden && !action)) return;
    if (busy) {
      if (action && pollController) { pendingAction = () => loadGame(action, params); pollController.abort(); }
      return;
    }
    const currentId = matchId;
    const initial = !game;
    const sources = {
      player: [...get('hand').children].find(card => card.dataset.duelCardId === params.card_id)?.getBoundingClientRect(),
      opponent: get('opponent-cards').lastElementChild?.getBoundingClientRect(),
    };
    if (action) stopPlayback();
    busy = true;
    if (action) {
      selectedCard = null;
      get('colors').hidden = true;
      renderGame();
    }
    if (action === 'draw') {
      const pending = get('draw').cloneNode(true);
      pending.removeAttribute('data-duel-draw');
      pending.setAttribute('aria-label', 'Robando una carta');
      pending.disabled = true;
      get('hand').append(pending);
      message('Robando una carta…');
    }
    const controller = action ? null : new AbortController();
    if (controller) pollController = controller;
    try {
      const after = game ? game.version : 0;
      const url = gameUrl(currentId);
      const data = action
        ? await request(url, { action, expected_version: game.version, after_version: after, ...params })
        : await request(`${url}?after_version=${after}`, null, controller.signal);
      if (matchId !== currentId) return;
      if (!action && game && data.game.version === game.version) return;
      game = data.game;
      busy = false;
      renderGame();
      if (!game.winner) message(game.drawn ? 'Podés jugar la carta que robaste o pasar.' : game.turn === 'player' ? 'Elegí una carta o robá del mazo.' : `Esperando a ${opponent}…`);
      if (!initial && game.events.length) {
        stopPlayback();
        showEvents(game.events, sources, playback).catch(() => { get('flash').hidden = true; });
      }
    } catch (error) {
      if (matchId === currentId && error.name !== 'AbortError') message(error.message);
    } finally {
      if (pollController === controller) pollController = null;
      busy = false;
      if (action && matchId === currentId) renderGame();
      if (pendingAction) { const next = pendingAction; pendingAction = null; next(); }
      else schedule();
    }
  }

  function sendGame(action, params = {}) { loadGame(action, params); }
  get('invite').addEventListener('submit', event => {
    event.preventDefault();
    const form = new FormData(get('invite'));
    lobbyAction({ action: 'invite', proposal_id: form.get('proposal_id'), invitee_id: form.get('invitee_id') });
  });
  get('draw').addEventListener('click', () => sendGame('draw'));
  get('pass').addEventListener('click', () => sendGame('pass'));
  get('forfeit').addEventListener('click', () => {
    if (window.confirm('¿Querés abandonar? Tu rival ganará y sumará un punto para su propuesta.')) sendGame('forfeit');
  });
  get('back').addEventListener('click', () => {
    if (busy && !pollController) return;
    if (pollController) pollController.abort();
    pendingAction = null;
    stopPlayback(); matchId = null; game = null; lastLobby = '';
    get('board').hidden = true; get('lobby').hidden = false; message(''); loadLobby();
  });
  get('cancel').addEventListener('click', () => { selectedCard = null; get('colors').hidden = true; });
  root.querySelectorAll('[data-duel-choose]').forEach(element => element.addEventListener('click', () => {
    if (selectedCard) sendGame('play', { card_id: selectedCard, color: element.dataset.duelChoose });
  }));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(); });
  document.addEventListener('uno-mode-change', event => {
    clearTimeout(timer);
    if (event.detail.mode !== 'grupo' || busy) return;
    if (matchId) loadGame();
    else loadLobby();
  });
  loadLobby();
})();
