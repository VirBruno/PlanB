(() => {
  const game = document.querySelector("[data-combat-game]");
  const panel = game?.querySelector("[data-combat-truco]");
  if (!panel) return;

  const startButton = panel.querySelector("[data-truco-start]");
  const playerScoreLabel = panel.querySelector("[data-truco-player-score]");
  const botScoreLabel = panel.querySelector("[data-truco-bot-score]");
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
  const cardRanks = { 1: "1", 10: "10", 11: "11", 12: "12" };
  const faceNames = { 1: "Ancho", 10: "Sota", 11: "Caballo", 12: "Rey" };
  const svgNamespace = "http://www.w3.org/2000/svg";
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
  };
  const clearCalls = () => {
    for (const attribute of [
      "data-truco-envido", "data-truco-real", "data-truco-falta",
      "data-truco-truco", "data-truco-retruco", "data-truco-vale",
      "data-truco-accept", "data-truco-reject",
    ]) hide(button(attribute), true);
  };
  const scoreMatch = (winner, points) => {
    if (winner === "player") playerScore = Math.min(30, playerScore + points);
    else botScore = Math.min(30, botScore + points);
    setScores();
  };
  const createCardFace = (card, className = "proposal-truco-card") => {
    const face = document.createElement("div");
    face.className = `${className} proposal-truco-card--${card.suit}`;
    face.setAttribute("role", "img");
    face.setAttribute("aria-label", cardName(card));

    const addSpan = (parent, className, text = "") => {
      const span = document.createElement("span");
      span.className = className;
      span.textContent = text;
      parent.append(span);
      return span;
    };
    const makeSuitIcon = () => {
      const svg = document.createElementNS(svgNamespace, "svg");
      svg.setAttribute("class", "proposal-truco-suit-icon");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      const paths = {
        copa: "M5 3h14l-1.6 8a5.5 5.5 0 0 1-4.4 4.4V19h4v2H7v-2h4v-3.6A5.5 5.5 0 0 1 6.6 11L5 3Zm14 2h2v3a3 3 0 0 1-3 3l.4-2a1 1 0 0 0 .6-1V5ZM5 5H3v3a3 3 0 0 0 3 3l-.4-2A1 1 0 0 1 5 8V5Z",
        oro: "M12 1.5a10.5 10.5 0 1 0 0 21 10.5 10.5 0 0 0 0-21Z",
        basto: "M7 23 9.5 16 6 13l2-2-2-3 2-2-1-2 3-2 2 2 3-1 2 3-2 2 2 3-3 3-2 9Z",
        espada: "M12 1 17 12 14 14 13 17 18 18 18 20 13 19 13 23 11 23 11 19 6 20 6 18 11 17 10 14 7 12Z",
      };
      const path = document.createElementNS(svgNamespace, "path");
      path.setAttribute("d", paths[card.suit]);
      svg.append(path);
      if (card.suit === "oro") {
        const innerCoin = document.createElementNS(svgNamespace, "circle");
        innerCoin.setAttribute("cx", "12");
        innerCoin.setAttribute("cy", "12");
        innerCoin.setAttribute("r", "7.5");
        innerCoin.setAttribute("fill", "none");
        innerCoin.setAttribute("stroke", "#fff4ce");
        innerCoin.setAttribute("stroke-width", "1.1");
        svg.append(innerCoin);
        const mark = document.createElementNS(svgNamespace, "path");
        mark.setAttribute("d", "M12 7.5v9m-2.5-6.8h4.2a1.7 1.7 0 0 1 0 3.4h-3.4a1.7 1.7 0 0 0 0 3.4h4.2");
        mark.setAttribute("fill", "none");
        mark.setAttribute("stroke", "#fff4ce");
        mark.setAttribute("stroke-width", "1");
        mark.setAttribute("stroke-linecap", "round");
        svg.append(mark);
      }
      if (card.suit === "basto") {
        const knots = document.createElementNS(svgNamespace, "path");
        knots.setAttribute("d", "m8 8 5 3m-6 2 5 3m1-11 3 3");
        knots.setAttribute("fill", "none");
        knots.setAttribute("stroke", "#e7f2c5");
        knots.setAttribute("stroke-width", "1");
        knots.setAttribute("stroke-linecap", "round");
        svg.append(knots);
      }
      return svg;
    };
    const makePip = (small = false) => {
      const pip = document.createElement("span");
      pip.className = `proposal-truco-pip${small ? " proposal-truco-pip--small" : ""}`;
      pip.append(makeSuitIcon());
      return pip;
    };
    const makeCourtFigure = (mirror = false) => {
      const figure = document.createElement("span");
      const headwear = { 10: "cap", 11: "helmet", 12: "crown" }[card.value];
      figure.className = `proposal-truco-court-figure proposal-truco-court-figure--${card.value} proposal-truco-court-figure--${card.suit}${mirror ? " proposal-truco-court-figure--mirror" : ""}`;
      for (const piece of [headwear, "head", "body", "sash"]) {
        const detail = document.createElement("i");
        detail.className = `proposal-truco-court-${piece}`;
        figure.append(detail);
      }
      if (card.value === 11) {
        const horse = document.createElement("i");
        horse.className = "proposal-truco-court-horse";
        figure.append(horse);
      }
      return figure;
    };

    const rank = cardRanks[card.value] || String(card.value);
    const topCorner = addSpan(face, "proposal-truco-card-corner proposal-truco-card-corner--top", rank);
    topCorner.append(makePip(true));
    const content = addSpan(face, `proposal-truco-card-content${card.value > 7 ? " proposal-truco-card-content--court" : ""}`);
    if (card.value <= 7) {
      const pips = addSpan(content, `proposal-truco-pips proposal-truco-pips--${card.value}`);
      for (let index = 0; index < card.value; index += 1) pips.append(makePip());
    } else {
      const court = addSpan(content, `proposal-truco-court proposal-truco-court--${card.value}`);
      court.append(makeCourtFigure(), makeCourtFigure(true));
      addSpan(court, "proposal-truco-court-title", faceNames[card.value]);
    }
    const bottomCorner = addSpan(face, "proposal-truco-card-corner proposal-truco-card-corner--bottom", rank);
    bottomCorner.append(makePip(true));
    return face;
  };
  const renderPlayedCard = (played, element) => {
    element.replaceChildren();
    if (!played) return;
    const card = createCardFace(played.card, "proposal-truco-card proposal-truco-card--played");
    if (played.animate) {
      card.className += " is-playing";
      played.animate = false;
    }
    element.append(card);
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
    hand = null;
    clearCalls();
    startButton.hidden = false;
    startButton.textContent = "Siguiente mano";
    render();
  };
  const render = () => {
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
    if (matchOver || !hand) return;
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
    const [card] = cards.splice(index, 1);
    if (player === "player") hand.playerPlayed = true;
    else hand.botPlayed = true;
    hand.table.push({ player, card, animate: true });
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
