(() => {
  const game = document.querySelector("[data-combat-game]");
  const panel = game?.querySelector("[data-combat-truco]");
  if (!panel) return;

  const startButton = panel.querySelector("[data-truco-start]");
  const playerScoreLabel = panel.querySelector("[data-truco-player-score]");
  const botScoreLabel = panel.querySelector("[data-truco-bot-score]");
  const playerTallies = panel.querySelectorAll("[data-truco-player-tally]");
  const botTallies = panel.querySelectorAll("[data-truco-bot-tally]");
  const pointNotice = panel.querySelector("[data-truco-point-notice]");
  const arena = panel.querySelector(".proposal-truco-arena");
  const felt = panel.querySelector(".proposal-truco-felt");
  const botHandElement = panel.querySelector("[data-truco-bot-hand]");
  const trickLabel = panel.querySelector("[data-truco-trick]");
  const handElement = panel.querySelector("[data-truco-hand]");
  const playedBoard = panel.querySelector("[data-truco-drop-zone]");
  const playedBotElement = panel.querySelector("[data-truco-played-bot]");
  const playedPlayerElement = panel.querySelector("[data-truco-played-player]");
  const status = panel.querySelector("[data-truco-status]");
  const saveForm = panel.querySelector("[data-truco-save]");
  const scoreInput = panel.querySelector("[data-truco-score-input]");
  if (!startButton || !playerScoreLabel || !botScoreLabel || !botHandElement
      || !arena || !felt || !handElement || !playedBoard || !playedBotElement
      || !playedPlayerElement || !saveForm || !scoreInput) return;

  const suits = ["espada", "basto", "oro", "copa"];
  const suitNames = { espada: "espada", basto: "basto", oro: "oro", copa: "copa" };
  const names = { 1: "ancho", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 10: "sota", 11: "caballo", 12: "rey" };
  const strength = (card) => {
    if (card.value === 1 && card.suit === "espada") return 14;
    if (card.value === 1 && card.suit === "basto") return 13;
    if (card.value === 7 && card.suit === "espada") return 12;
    if (card.value === 7 && card.suit === "oro") return 11;
    if (card.value === 3) return 10;
    if (card.value === 2) return 9;
    if (card.value === 1) return 8;
    if (card.value === 12) return 7;
    if (card.value === 11) return 6;
    if (card.value === 10) return 5;
    if (card.value === 7) return 4;
    return card.value - 3;
  };
  const envidoValue = (cards) => {
    let best = 0;
    for (const suit of suits) {
      const matching = cards.filter((card) => card.suit === suit)
        .map((card) => card.value >= 10 ? 0 : card.value);
      if (matching.length >= 2) {
        matching.sort((a, b) => b - a);
        best = Math.max(best, 20 + matching[0] + matching[1]);
      } else if (matching.length === 1) {
        best = Math.max(best, matching[0]);
      }
    }
    return best;
  };
  const cardName = (card) => `${names[card.value]} de ${suitNames[card.suit]}`;
  const makeDeck = () => suits.flatMap((suit) =>
    [1, 2, 3, 4, 5, 6, 7, 10, 11, 12].map((value) => ({ suit, value })),
  );
  const shuffle = (cards) => {
    for (let index = cards.length - 1; index > 0; index -= 1) {
      const other = Math.floor(Math.random() * (index + 1));
      [cards[index], cards[other]] = [cards[other], cards[index]];
    }
    return cards;
  };

  let playerScore = 0;
  let botScore = 0;
  let humanMano = true;
  let hand = null;
  let matchOver = true;
  let handFinished = false;
  const noticeQueue = [];
  let noticeActive = false;
  let draggedCardIndex = null;
  let draggedCardButton = null;

  const makeHand = () => {
    const deck = shuffle(makeDeck());
    const cards = deck.splice(0, 3);
    const botCards = deck.splice(0, 3);
    return {
      cards, botCards, playerPlayed: false, botPlayed: false,
      turn: humanMano ? "player" : "bot", tricks: [], table: [],
      stake: 1, trucoLevel: 0, pending: null, envidoResolved: false,
      trickNumber: 1, dealAnimationPending: true,
    };
  };

  const button = (attribute) => panel.querySelector(`[${attribute}]`);
  const hide = (element, value) => {
    if (element) element.hidden = value;
  };
  const setStatus = (message) => { status.textContent = message; };
  const setScores = () => {
    playerScoreLabel.textContent = String(playerScore);
    botScoreLabel.textContent = String(botScore);
    for (const [element, score] of [
      [playerTallies[0], Math.min(playerScore, 15)], [botTallies[0], Math.min(botScore, 15)],
      [playerTallies[1], Math.max(playerScore - 15, 0)], [botTallies[1], Math.max(botScore - 15, 0)],
    ]) {
      if (!element) continue;
      element.replaceChildren();
      for (let group = 0; group < 3; group += 1) {
        const marks = document.createElement("span");
        marks.className = "proposal-truco-tally-group";
        for (let mark = 0; mark < 5; mark += 1) {
          const stroke = document.createElement("i");
          if (group * 5 + mark < score) stroke.className = "is-scored";
          marks.append(stroke);
        }
        element.append(marks);
      }
    }
  };
  const clearCalls = () => {
    for (const attribute of [
      "data-truco-envido", "data-truco-real", "data-truco-falta",
      "data-truco-truco", "data-truco-retruco", "data-truco-vale",
      "data-truco-accept", "data-truco-reject",
    ]) hide(button(attribute), true);
  };
  const announce = (message, kind, who) => {
    if (!pointNotice) return;
    noticeQueue.push({ message, kind, who });
    if (noticeActive) return;
    const showNext = () => {
      const next = noticeQueue.shift();
      if (!next) {
        pointNotice.hidden = true;
        noticeActive = false;
        return;
      }
      noticeActive = true;
      pointNotice.hidden = false;
      pointNotice.dataset.kind = next.kind;
      pointNotice.replaceChildren();
      const label = document.createElement("small");
      label.textContent = next.who;
      const title = document.createElement("strong");
      title.textContent = next.message;
      pointNotice.append(label, title);
      pointNotice.classList.remove("is-visible");
      void pointNotice.offsetWidth;
      pointNotice.classList.add("is-visible");
      window.setTimeout(showNext, next.kind === "points" ? 1700 : 950);
    };
    showNext();
  };
  const scoreMatch = (winner, points) => {
    if (winner === "player") playerScore = Math.min(30, playerScore + points);
    else botScore = Math.min(30, botScore + points);
    setScores();
    announce(`+${points} punto${points === 1 ? "" : "s"}`, "points", winner === "player" ? "Sumaste vos" : "Sumó el bot");
  };
  const cardCorners = {
    oro: {"1":[17.768,12.066,86.788,13.081,16.212,117.919],"2":[16.714,12.104,80.804,12.604,15.946,110.396],"3":[16.11,12.408,83.39,12.408,16.11,115.592],"4":[15.458,10.796,81.578,11.798,13.922,112.202],"5":[21.426,23.642,80.586,24.628,19.914,114.372],"6":[16.392,12.398,83.672,13.418,14.828,115.582],"7":[16.927,16.157,81.597,16.662,16.153,115.338],"10":[16.342,17.77,82.172,18.269,15.578,118.731],"11":[18.392,16.398,85.672,17.418,16.828,119.582],"12":[18.806,14.323,85.216,14.827,18.034,116.173]},
    copa: {"1":[17.9,13.353,84.6,13.353,17.9,115.647],"2":[19.705,15.961,82.345,16.971,18.155,112.029],"3":[19.72,16.411,81.78,16.411,19.72,111.589],"4":[20.923,15.7,86.173,17.229,18.577,115.771],"5":[16.946,16.604,81.036,16.104,17.714,114.896],"6":[17.549,16.49,80.479,16.997,16.771,113.003],"7":[17.049,16.99,79.979,17.497,16.271,113.503],"10":[20.049,16.49,82.979,16.997,19.271,113.003],"11":[17.464,17.604,81.554,18.104,16.696,115.896],"12":[18.672,16.511,81.892,17.531,17.108,113.469]},
    espada: {"1":[24.408,17.831,85.598,18.325,23.652,111.675],"2":[19.114,17.578,82.914,18.575,17.586,115.425],"3":[17.927,13.657,82.597,14.162,17.153,112.838],"4":[18.319,18.748,78.639,17.742,19.861,111.258],"5":[16.24,19.047,79.75,18.551,17.0,116.449],"6":[15.3,18.3,76.2,18.3,15.3,111.7],"7":[17.379,16.217,82.629,16.712,16.621,116.288],"10":[22.077,18.482,85.007,20.005,19.743,114.995],"11":[17.586,17.937,79.936,18.44,16.814,113.56],"12":[18.513,19.543,82.023,20.055,17.727,116.945]},
    basto: {"1":[20.763,15.543,84.273,16.055,19.977,112.945],"2":[21.803,17.809,82.703,18.791,20.297,111.209],"3":[20.394,18.192,80.134,19.188,18.866,109.812],"4":[17.357,14.272,83.187,15.768,15.063,115.232],"5":[18.879,13.217,84.129,13.712,18.121,113.288],"6":[15.233,11.94,82.803,13.431,12.947,115.569],"7":[16.371,14.712,81.621,14.217,17.129,114.783],"10":[18.458,12.796,84.578,13.798,16.922,114.202],"11":[22.577,16.982,85.507,18.505,20.243,113.495],"12":[20.8,18.3,81.7,18.3,20.8,111.7]},
  };
  const cardSuitNames = { oro: "Oros", copa: "Copas", espada: "Espadas", basto: "Bastos" };
  const cardImages = new Map();
  const cardArtData = new WeakMap();
  const drawCardArt = (canvas, card) => {
    const [left, top, right, rightTop, bottomLeft, bottom] = cardCorners[card.suit][card.value];
    const filename = `Heraclio_Fournier_${card.value}_${cardSuitNames[card.suit]}.jpg`;
    if (!cardImages.has(filename)) {
      const image = new Image();
      image.src = `${panel.dataset.trucoCardBase}${filename}?v=2`;
      cardImages.set(filename, image);
    }
    const image = cardImages.get(filename);
    const draw = () => {
      if (!canvas.isConnected || !image.naturalWidth) return;
      const scale = Math.max(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * scale));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * scale));
      const context = canvas.getContext("2d");
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = "high";
      // Map three photographed card corners to a straight rectangle.
      const x0 = left * image.naturalWidth / 100;
      const y0 = top * image.naturalHeight / 133;
      const dx1 = (right - left) * image.naturalWidth / 100;
      const dy1 = (rightTop - top) * image.naturalHeight / 133;
      const dx2 = (bottomLeft - left) * image.naturalWidth / 100;
      const dy2 = (bottom - top) * image.naturalHeight / 133;
      const determinant = dx1 * dy2 - dx2 * dy1;
      const a = canvas.width * dy2 / determinant;
      const c = -canvas.width * dx2 / determinant;
      const b = -canvas.height * dy1 / determinant;
      const d = canvas.height * dx1 / determinant;
      context.setTransform(a, b, c, d, -a * x0 - c * y0, -b * x0 - d * y0);
      context.drawImage(image, 0, 0);
    };
    if (image.complete) window.requestAnimationFrame(draw);
    else image.addEventListener("load", () => window.requestAnimationFrame(draw), { once: true });
  };
  const createCardFace = (card, className = "proposal-truco-card") => {
    const face = document.createElement("div");
    face.className = `${className} proposal-truco-card--${card.suit}`;
    face.setAttribute("role", "img");
    face.setAttribute("aria-label", cardName(card));

    const art = document.createElement("canvas");
    art.className = "proposal-truco-card-art";
    art.setAttribute("aria-hidden", "true");
    face.append(art);
    cardArtData.set(art, card);
    drawCardArt(art, card);
    return face;
  };
  const renderPlayedCard = (played, element) => {
    element.replaceChildren();
    if (!played) return;
    const card = createCardFace(played.card, "proposal-truco-card proposal-truco-card--played");
    element.append(card);
    if (played.animate) {
      played.animate = false;
      const from = played.origin;
      const to = card.getBoundingClientRect();
      if (from && from.width && from.height && to.width && to.height && card.animate
          && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        card.animate([
          { transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})` },
          { transform: "translate(0, 0) scale(1, 1)" },
        ], { duration: 380, easing: "ease-in-out" });
      }
    }
  };
  const finishMatch = (winner) => {
    matchOver = true;
    clearCalls();
    startButton.hidden = false;
    startButton.textContent = "Nueva partida";
    handElement.replaceChildren();
    if (winner === "player") {
      scoreInput.value = String(playerScore);
      saveForm.hidden = false;
      setStatus(`¡Ganaste el Truco! Resultado final: ${playerScore} a ${botScore}. Guardá tus puntos.`);
    } else {
      setStatus(`Ganó el bot: ${botScore} a ${playerScore}. Resultado guardado para el marcador de Truco.`);
    }
    scoreInput.value = String(playerScore);
    saveForm.hidden = false;
    render();
  };
  const finishHand = (winner, message, points = hand.stake) => {
    scoreMatch(winner, points);
    setStatus(`${message} ${winner === "player" ? "Sumás" : "El bot suma"} ${points} punto${points === 1 ? "" : "s"}.`);
    if (playerScore >= 30 || botScore >= 30) {
      finishMatch(playerScore >= 30 ? "player" : "bot");
      return;
    }
    humanMano = !humanMano;
    handFinished = true;
    hand.turn = null;
    hand.pending = null;
    clearCalls();
    startButton.hidden = true;
    render();
    window.setTimeout(() => {
      if (!handFinished || matchOver) return;
      startHand();
    }, 2600);
  };
  const render = () => {
    panel.dataset.trucoState = matchOver
      ? (playerScore || botScore ? "finished" : "ready")
      : (handFinished ? "between-hands" : hand ? "playing" : "between-hands");
    setScores();
    handElement.replaceChildren();
    botHandElement.replaceChildren();
    playedBotElement.replaceChildren();
    playedPlayerElement.replaceChildren();
    if (hand) {
      botHandElement.setAttribute(
        "aria-label",
        `Cartas del bot: ${hand.botCards.length} sin jugar.`,
      );
      for (const [index, card] of hand.cards.entries()) {
        const cardButton = document.createElement("button");
        cardButton.type = "button";
        const face = createCardFace(card);
        cardButton.className = face.className;
        cardButton.append(...face.children);
        cardButton.setAttribute("aria-label", `Jugar ${cardName(card)}`);
        cardButton.disabled = matchOver || hand.turn !== "player" || Boolean(hand.pending);
        cardButton.draggable = !cardButton.disabled;
        cardButton.addEventListener("click", () => {
          if (draggedCardButton === cardButton) return;
          playCard("player", index);
        });
        cardButton.addEventListener("dragstart", (event) => {
          if (cardButton.disabled || !hand || hand.turn !== "player" || hand.pending) {
            event.preventDefault();
            return;
          }
          draggedCardIndex = index;
          draggedCardButton = cardButton;
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", String(index));
        });
        cardButton.addEventListener("dragend", () => {
          arena.classList.remove("is-drop-target");
          draggedCardIndex = null;
          window.setTimeout(() => {
            if (draggedCardButton === cardButton) draggedCardButton = null;
          }, 0);
        });
        if (hand.dealAnimationPending) cardButton.className += " is-dealing";
        handElement.append(cardButton);
      }
      for (let index = 0; index < hand.botCards.length; index += 1) {
        const back = document.createElement("span");
        back.className = "proposal-truco-card-back";
        back.setAttribute("aria-hidden", "true");
        if (hand.dealAnimationPending) back.className += " is-dealing";
        botHandElement.append(back);
      }
      hand.dealAnimationPending = false;
      for (const played of hand.table) {
        renderPlayedCard(
          played,
          played.player === "bot" ? playedBotElement : playedPlayerElement,
        );
      }
      if (hand.table.length) {
        const nextPlayer = hand.turn === "player" ? "Tu turno" : hand.turn === "bot" ? "Juega el bot" : "Baza en curso";
        trickLabel.textContent = `Mano ${hand.trickNumber} · ${nextPlayer}`;
      } else {
        trickLabel.textContent = `Mano ${hand.trickNumber} · Vale ${hand.stake} punto${hand.stake === 1 ? "" : "s"}.`;
      }
    } else {
      botHandElement.setAttribute("aria-label", "Cartas del bot");
      trickLabel.textContent = "Repartí para empezar.";
    }
    renderCalls();
  };
  const showTrucoCall = (level) => {
    if (level === 0) hide(button("data-truco-truco"), false);
    if (level === 1) hide(button("data-truco-retruco"), false);
    if (level === 2) hide(button("data-truco-vale"), false);
  };
  const showEnvidoCall = (level) => {
    if (level < 1) hide(button("data-truco-envido"), false);
    if (level < 2) hide(button("data-truco-real"), false);
    if (level < 3) hide(button("data-truco-falta"), false);
  };
  const renderCalls = () => {
    clearCalls();
    if (matchOver || !hand || handFinished) return;
    const pending = hand.pending;
    if (pending) {
      if (pending.caller !== "player") {
        hide(button("data-truco-accept"), false);
        hide(button("data-truco-reject"), false);
        if (pending.kind === "envido") showEnvidoCall(pending.level);
        else if (pending.level < 3) showTrucoCall(pending.level);
      }
      return;
    }
    if (hand.turn !== "player") return;
    if (!hand.envidoResolved && !hand.playerPlayed) showEnvidoCall(0);
    if (hand.trucoLevel < 3) showTrucoCall(hand.trucoLevel);
  };

  const startHand = () => {
    if (matchOver) return;
    handFinished = false;
    hand = makeHand();
    startButton.hidden = true;
    setStatus(`${humanMano ? "Sos mano." : "El bot es mano."} Jugá tus cartas; el primero en llegar a 30 gana.`);
    render();
    if (hand.turn === "bot") queueBotTurn();
  };
  const startMatch = () => {
    playerScore = 0;
    botScore = 0;
    humanMano = true;
    hand = null;
    matchOver = false;
    saveForm.hidden = true;
    startButton.textContent = "Repartir";
    setStatus("Partida nueva. Gana quien llegue a 30 puntos.");
    setScores();
    startHand();
  };

  const botEnvidoThreshold = () => envidoValue(hand.botCards) >= 27;
  const callEnvido = (caller, level) => {
    if (hand.envidoResolved) return;
    announce(`¡${level === 3 ? "Falta envido" : level === 2 ? "Real envido" : "Envido"}!`, "envido", caller === "player" ? "Vos cantaste" : "Canta el bot");
    const previous = hand.pending?.kind === "envido" ? hand.pending : null;
    const callerScore = caller === "player" ? playerScore : botScore;
    const target = level === 3
      ? 30 - callerScore
      : (previous?.value || 0) + (level === 2 ? 3 : 2);
    const value = Math.min(30 - callerScore, target);
    hand.pending = {
      kind: "envido", caller, level, value: Math.max(1, value),
      refusalValue: previous ? previous.value : 1,
    };
    if (caller === "player") {
      setStatus(level === 3 ? "Cantaste Falta Envido. El bot decide si quiere." : "Cantaste Envido. El bot decide si quiere.");
      const botValue = envidoValue(hand.botCards);
      if (botValue >= 25 || (level === 3 && botValue >= 20)) {
        hand.pending.acceptByBot = true;
      }
      window.setTimeout(() => respondToBotEnvido(), 350);
    } else {
      setStatus(`El bot cantó ${level === 3 ? "Falta Envido" : level === 2 ? "Real Envido" : "Envido"}. ¿Qué respondés?`);
    }
    render();
  };
  const respondToBotEnvido = () => {
    if (!hand?.pending || hand.pending.kind !== "envido" || hand.pending.caller !== "player") return;
    const pending = hand.pending;
    if (pending.acceptByBot) acceptBid();
    else rejectBid();
  };
  const callTruco = (caller, level) => {
    announce(`¡${level === 1 ? "Truco" : level === 2 ? "Retruco" : "Vale cuatro"}!`, "truco", caller === "player" ? "Vos cantaste" : "Canta el bot");
    const previousStake = hand.stake;
    const value = level + 1;
    hand.pending = { kind: "truco", caller, level, value, refusalValue: previousStake };
    setStatus(caller === "player" ? `Cantaste ${level === 1 ? "Truco" : level === 2 ? "Retruco" : "Vale Cuatro"}.` : `El bot cantó ${level === 1 ? "Truco" : level === 2 ? "Retruco" : "Vale Cuatro"}.`);
    if (caller === "player") {
      const bestCard = Math.max(...hand.botCards.map(strength));
      if (bestCard >= 10 || (bestCard >= 8 && Math.random() > 0.35)) {
        window.setTimeout(acceptBid, 350);
      } else {
        window.setTimeout(rejectBid, 350);
      }
    }
    render();
  };
  const acceptBid = () => {
    if (!hand?.pending) return;
    const pending = hand.pending;
    announce("¡Quiero!", "answer", pending.caller === "player" ? "Respondió el bot" : "Respondiste vos");
    if (pending.kind === "envido") {
      const playerValue = envidoValue(hand.cards);
      const botValue = envidoValue(hand.botCards);
      const playerWins = playerValue === botValue ? humanMano : playerValue > botValue;
      scoreMatch(playerWins ? "player" : "bot", pending.value);
      hand.envidoResolved = true;
      hand.pending = null;
      hand.turn = pending.caller;
      setStatus(`Envido: vos ${playerValue}, bot ${botValue}. ${playerWins ? "Ganaste el Envido." : "El bot ganó el Envido."}`);
      if (playerScore >= 30 || botScore >= 30) finishMatch(playerScore >= 30 ? "player" : "bot");
      else {
        render();
        if (hand.turn === "bot") queueBotTurn();
      }
    } else {
      hand.stake = pending.value;
      hand.trucoLevel = pending.level;
      hand.turn = pending.caller;
      hand.pending = null;
      setStatus(`Quiero. La mano vale ${hand.stake}.`);
      render();
      if (hand.turn === "bot") queueBotTurn();
    }
  };
  const rejectBid = () => {
    if (!hand?.pending) return;
    const pending = hand.pending;
    announce("No quiero", "answer", pending.caller === "player" ? "Respondió el bot" : "Respondiste vos");
    const winner = pending.caller;
    hand.pending = null;
    if (pending.kind === "envido") {
      scoreMatch(winner, pending.refusalValue);
      hand.envidoResolved = true;
      hand.turn = winner;
      setStatus(`No quiero. ${winner === "player" ? "Sumás" : "El bot suma"} ${pending.refusalValue} punto${pending.refusalValue === 1 ? "" : "s"} de Envido.`);
      if (playerScore >= 30 || botScore >= 30) finishMatch(playerScore >= 30 ? "player" : "bot");
      else {
        render();
        if (hand.turn === "bot") queueBotTurn();
      }
    } else {
      finishHand(
        winner,
        pending.caller === "player" ? "El bot no quiso." : "No quisiste.",
        pending.refusalValue,
      );
    }
  };

  const compareTrick = () => {
    const first = hand.table[0];
    const second = hand.table[1];
    const firstStrength = strength(first.card);
    const secondStrength = strength(second.card);
    const winner = firstStrength === secondStrength ? null
      : firstStrength > secondStrength ? first.player : second.player;
    hand.tricks.push(winner);
    hand.table = [];
    if (hand.tricks.filter((team) => team === "player").length >= 2) {
      finishHand("player", "Ganaste dos bazas.");
      return;
    }
    if (hand.tricks.filter((team) => team === "bot").length >= 2) {
      finishHand("bot", "El bot ganó dos bazas.");
      return;
    }
    if (winner) hand.turn = winner;
    else if (hand.tricks.length === 1) hand.turn = humanMano ? "player" : "bot";
    else if (hand.tricks.length === 2 && hand.tricks[0]) hand.turn = hand.tricks[0];
    else hand.turn = humanMano ? "player" : "bot";
    hand.trickNumber += 1;
    if (hand.tricks.length === 3) {
      const winnerOfHand = hand.tricks[0] || hand.tricks[1] || hand.tricks[2] || (humanMano ? "player" : "bot");
      finishHand(winnerOfHand, "Se jugaron las tres bazas.");
      return;
    }
    render();
    if (hand.turn === "bot") queueBotTurn();
  };
  const playCard = (player, index) => {
    if (!hand || hand.turn !== player || hand.pending) return;
    const cards = player === "player" ? hand.cards : hand.botCards;
    const source = player === "player" ? handElement.children[index] : botHandElement.children[index];
    const origin = source?.getBoundingClientRect();
    const [card] = cards.splice(index, 1);
    if (player === "player") hand.playerPlayed = true;
    else hand.botPlayed = true;
    hand.table.push({ player, card, animate: true, origin });
    if (hand.table.length === 2) {
      hand.turn = null;
      setStatus("Se compara la baza…");
      render();
      window.setTimeout(compareTrick, 850);
      return;
    }
    hand.turn = player === "player" ? "bot" : "player";
    setStatus(player === "player" ? "El bot juega su carta." : "Tu turno: elegí una carta.");
    render();
    if (hand.turn === "bot") queueBotTurn();
  };

  const isBoardDropTarget = (target) => (
    target === felt || target === playedBoard || playedBoard.contains(target)
  );
  arena.addEventListener("dragover", (event) => {
    if (!hand || hand.turn !== "player" || hand.pending || draggedCardIndex === null
        || !isBoardDropTarget(event.target)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    arena.classList.add("is-drop-target");
  });
  arena.addEventListener("dragleave", (event) => {
    if (!arena.contains(event.relatedTarget)) arena.classList.remove("is-drop-target");
  });
  arena.addEventListener("drop", (event) => {
    arena.classList.remove("is-drop-target");
    if (!hand || hand.turn !== "player" || hand.pending || draggedCardIndex === null) return;
    if (!isBoardDropTarget(event.target)) return;
    event.preventDefault();
    const droppedIndex = Number(event.dataTransfer.getData("text/plain"));
    if (droppedIndex !== draggedCardIndex || !hand.cards[droppedIndex]) return;
    playCard("player", droppedIndex);
  });

  const queueBotTurn = () => {
    window.setTimeout(botTurn, 450);
  };
  const botTurn = () => {
    if (!hand || matchOver || hand.turn !== "bot" || hand.pending) return;
    if (!hand.envidoResolved && !hand.botPlayed && botEnvidoThreshold() && Math.random() > 0.55) {
      callEnvido("bot", 1);
      return;
    }
    const bestCard = Math.max(...hand.botCards.map(strength));
    if (hand.trucoLevel === 0 && bestCard >= 12 && Math.random() > 0.65) {
      callTruco("bot", 1);
      return;
    }
    let index = Math.floor(Math.random() * hand.botCards.length);
    if (hand.table.length) {
      const opponentStrength = strength(hand.table[0].card);
      const winning = hand.botCards.map((card, cardIndex) => ({ card, cardIndex }))
        .filter(({ card }) => strength(card) > opponentStrength)
        .sort((a, b) => strength(a.card) - strength(b.card));
      const losing = hand.botCards.map((card, cardIndex) => ({ card, cardIndex }))
        .sort((a, b) => strength(a.card) - strength(b.card));
      if (winning.length) index = winning[0].cardIndex;
      else index = losing[0].cardIndex;
    } else {
      const weakest = hand.botCards.map((card, cardIndex) => ({ card, cardIndex }))
        .sort((a, b) => strength(a.card) - strength(b.card))[0];
      index = weakest.cardIndex;
    }
    playCard("bot", index);
  };

  startButton.addEventListener("click", () => {
    if (matchOver) startMatch();
    else if (!hand) startHand();
  });
  if (typeof ResizeObserver !== "undefined") {
    const cardResizeObserver = new ResizeObserver(() => {
      for (const art of panel.querySelectorAll(".proposal-truco-card-art")) {
        const card = cardArtData.get(art);
        if (card) drawCardArt(art, card);
      }
    });
    cardResizeObserver.observe(panel);
  }
  button("data-truco-envido")?.addEventListener("click", () => callEnvido("player", 1));
  button("data-truco-real")?.addEventListener("click", () => callEnvido("player", 2));
  button("data-truco-falta")?.addEventListener("click", () => callEnvido("player", 3));
  button("data-truco-truco")?.addEventListener("click", () => callTruco("player", 1));
  button("data-truco-retruco")?.addEventListener("click", () => callTruco("player", 2));
  button("data-truco-vale")?.addEventListener("click", () => callTruco("player", 3));
  button("data-truco-accept")?.addEventListener("click", acceptBid);
  button("data-truco-reject")?.addEventListener("click", rejectBid);
  clearCalls();
  render();
})();
