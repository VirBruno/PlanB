"""Reglas de una partida individual de UNO, sin acceso a datos externos.

El estado completo permanece del lado del servidor; ``public_state`` nunca
devuelve las cartas del bot ni el orden del mazo.
"""

import random
from uuid import uuid4


COLORS = ("rojo", "amarillo", "verde", "azul")
WILDS = ("comodin", "mas_cuatro")


class InvalidUnoMove(ValueError):
    """La jugada no es válida para el estado actual de la partida."""


def _deck():
    cards = []
    for color in COLORS:
        cards.append({"id": str(uuid4()), "color": color, "value": "0"})
        for value in (*map(str, range(1, 10)), "salto", "reversa", "mas_dos"):
            for _ in range(2):
                cards.append({"id": str(uuid4()), "color": color, "value": value})
    for value in WILDS:
        for _ in range(4):
            cards.append({"id": str(uuid4()), "color": None, "value": value})
    return cards


def new_game(*, rng=None):
    rng = rng or random.SystemRandom()
    deck = _deck()
    rng.shuffle(deck)
    player = [deck.pop() for _ in range(7)]
    bot = [deck.pop() for _ in range(7)]
    # La primera carta es numérica para que nadie reciba una penalización
    # antes de ver sus cartas.
    first_index = next(index for index, card in enumerate(deck) if card["value"].isdigit())
    first = deck.pop(first_index)
    return {"id": str(uuid4()), "deck": deck, "discard": [first],
            "player": player, "bot": bot, "color": first["color"],
            "turn": "player", "drawn": None, "winner": None, "announcements": [], "events": [],
            "message": "Elegí una carta o robá del mazo."}


def _draw(state, count, *, rng):
    drawn = []
    for _ in range(count):
        if not state["deck"]:
            if len(state["discard"]) <= 1:
                break
            top = state["discard"].pop()
            state["deck"] = state["discard"]
            state["discard"] = [top]
            rng.shuffle(state["deck"])
        drawn.append(state["deck"].pop())
    return drawn


def _playable(state, card, hand):
    if card["value"] == "mas_cuatro":
        return not any(other["color"] == state["color"] for other in hand)
    return (card["value"] == "comodin" or card["color"] == state["color"]
            or card["value"] == state["discard"][-1]["value"])


def _play(state, who, card, color, *, rng):
    hand = state[who]
    if not _playable(state, card, hand):
        raise InvalidUnoMove("Esa carta no se puede jugar ahora.")
    if card["value"] in WILDS and color not in COLORS:
        raise InvalidUnoMove("Elegí un color para el comodín.")
    hand.remove(card)
    state["discard"].append(card)
    state["color"] = color if card["value"] in WILDS else card["color"]
    state["drawn"] = None
    other = "bot" if who == "player" else "player"
    state.setdefault("events", []).append({"type": "play", "actor": who, "value": card["value"], "color": card["color"]})
    if card["value"] in ("mas_dos", "mas_cuatro"):
        amount = 2 if card["value"] == "mas_dos" else 4
        drawn = _draw(state, amount, rng=rng)
        state[other].extend(drawn)
        state["events"].append({"type": "draw", "actor": other, "count": len(drawn), "reason": card["value"]})
    if not hand:
        state["winner"] = who
        state["turn"] = None
        state["message"] = "¡Ganaste la partida!" if who == "player" else "El bot ganó la partida."
        return
    again = card["value"] in ("salto", "reversa", "mas_dos", "mas_cuatro")
    state["turn"] = who if again else other
    actor = "Jugaste" if who == "player" else "El bot jugó"
    state["message"] = f"{actor} {card['value'].replace('_', ' ')}."
    if len(hand) == 1:
        state.setdefault("announcements", []).append(
            "¡UNO! Te queda una carta." if who == "player" else "¡UNO! Al bot le queda una carta."
        )
        state["events"].append({"type": "uno", "actor": who})


def _bot_turns(state, *, rng):
    while state["turn"] == "bot" and not state["winner"]:
        playable = [card for card in state["bot"] if _playable(state, card, state["bot"])]
        if not playable:
            cards = _draw(state, 1, rng=rng)
            state["bot"].extend(cards)
            if cards:
                state.setdefault("events", []).append({"type": "draw", "actor": "bot", "count": 1, "reason": "deck"})
            playable = [card for card in cards if _playable(state, card, state["bot"])]
            if not playable:
                state["turn"] = "player"
                state["message"] = "El bot robó una carta. Es tu turno." if cards else "No hay cartas para robar. Es tu turno."
                break
        card = next((item for item in playable if item["value"] not in WILDS), playable[0])
        color = max(COLORS, key=lambda value: sum(c["color"] == value for c in state["bot"]))
        _play(state, "bot", card, color, rng=rng)


def act(state, action, *, card_id=None, color=None, rng=None):
    """Aplica una acción y los turnos del bot; modifica solo el estado recibido."""
    rng = rng or random.SystemRandom()
    if state["turn"] != "player" or state["winner"]:
        raise InvalidUnoMove("No hay un turno disponible.")
    state["announcements"] = []
    state["events"] = []
    if action == "play":
        card = next((card for card in state["player"] if card["id"] == card_id), None)
        if card is None or state["drawn"] and state["drawn"] != card_id:
            raise InvalidUnoMove("Esa carta no está disponible.")
        _play(state, "player", card, color, rng=rng)
    elif action == "draw":
        if state["drawn"]:
            raise InvalidUnoMove("Ya robaste una carta en este turno.")
        cards = _draw(state, 1, rng=rng)
        state["player"].extend(cards)
        if cards:
            state["events"].append({"type": "draw", "actor": "player", "count": 1, "reason": "deck"})
        if cards and _playable(state, cards[0], state["player"]):
            state["drawn"] = cards[0]["id"]
            state["message"] = "Podés jugar la carta que robaste o pasar."
        else:
            state["turn"] = "bot"
            state["message"] = "Robaste una carta."
    elif action == "pass" and state["drawn"]:
        state["drawn"] = None
        state["turn"] = "bot"
    else:
        raise InvalidUnoMove("Acción no disponible.")
    _bot_turns(state, rng=rng)
    return state


def public_state(state):
    return {"id": state["id"], "hand": [dict(card, playable=(
                state["turn"] == "player" and not state["winner"]
                and (not state["drawn"] or state["drawn"] == card["id"])
                and _playable(state, card, state["player"]))) for card in state["player"]],
            "top": state["discard"][-1], "color": state["color"],
            "bot_count": len(state["bot"]), "drawn": bool(state["drawn"]),
            "turn": state["turn"], "winner": state["winner"], "message": state["message"],
            "announcements": state.get("announcements", []), "events": state.get("events", [])}
