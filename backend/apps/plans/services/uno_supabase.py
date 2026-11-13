"""Adaptador del motor UNO de Supabase, todavía desactivado en la aplicación."""
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from . import plan_service, uno_game
from .proposal_exceptions import InvalidProposal, ProposalUnavailable


def _card(card, *, playable=False):
    if not isinstance(card, dict):
        raise ValueError
    identifier, color, value = card.get("id"), card.get("color"), card.get("value")
    UUID(str(identifier))
    if value not in (*map(str, range(10)), "salto", "reversa", "mas_dos", *uno_game.WILDS):
        raise ValueError
    if (value in uno_game.WILDS and color is not None) or (value not in uno_game.WILDS and color not in uno_game.COLORS):
        raise ValueError
    result = {"id": str(identifier), "color": color, "value": value}
    if playable:
        if type(card.get("playable")) is not bool:
            raise ValueError
        result["playable"] = card["playable"]
    return result


def _response(data):
    if not isinstance(data, dict) or type(data.get("victories")) is not int or data["victories"] < 0:
        raise ValueError
    game = data.get("game")
    if game is None:
        return {"game": None, "victories": data["victories"]}
    if not isinstance(game, dict) or not isinstance(game.get("hand"), list):
        raise ValueError
    UUID(str(game.get("id")))
    if (game.get("color") not in uno_game.COLORS or type(game.get("bot_count")) is not int
            or not 0 <= game["bot_count"] <= 108 or type(game.get("drawn")) is not bool
            or game.get("turn") not in ("player", "bot", None)
            or game.get("winner") not in ("player", "bot", None)
            or not isinstance(game.get("message"), str)
            or not isinstance(game.get("announcements"), list)
            or not all(isinstance(item, str) for item in game["announcements"])):
        raise ValueError
    visible = {key: game[key] for key in ("id", "color", "bot_count", "drawn", "turn", "winner", "message", "announcements")}
    visible["hand"] = [_card(card, playable=True) for card in game["hand"]]
    visible["top"] = _card(game.get("top"))
    events = game.get("events", [])
    if not isinstance(events, list):
        raise ValueError
    visible["events"] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in ("play", "draw", "uno") or event.get("actor") not in ("player", "bot"):
            raise ValueError
        safe = {"type": event["type"], "actor": event["actor"]}
        if event["type"] == "play":
            if event.get("value") not in (*map(str, range(10)), "salto", "reversa", "mas_dos", *uno_game.WILDS):
                raise ValueError
            safe["value"] = event["value"]
            color = event.get("color")
            if (safe["value"] in uno_game.WILDS and color is not None) or (safe["value"] not in uno_game.WILDS and color not in uno_game.COLORS):
                raise ValueError
            safe["color"] = color
        if event["type"] == "draw":
            if type(event.get("count")) is not int or not 0 <= event["count"] <= 4 or event.get("reason") not in ("deck", "mas_dos", "mas_cuatro"):
                raise ValueError
            safe.update(count=event["count"], reason=event["reason"])
        visible["events"].append(safe)
    return {"game": visible, "victories": data["victories"]}


@sensitive_variables()
def game(access_token, *, plan_id, proposal_id, action, card_id=None, color=None):
    """Llama al RPC con el JWT del integrante y filtra datos que no son públicos."""
    if not access_token:
        raise SessionExpired()
    plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    if type(proposal_id) is not int or proposal_id < 1 or action not in ("status", "start", "play", "draw", "pass"):
        raise InvalidProposal()
    if card_id is not None:
        try:
            card_id = str(UUID(str(card_id)))
        except (ValueError, TypeError, AttributeError):
            raise InvalidProposal() from None
    if color is not None and color not in uno_game.COLORS:
        raise InvalidProposal()
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("uno_bot_game", {
                "p_plan_id": plan_id, "p_proposal_id": proposal_id, "p_action": action,
                "p_card_id": card_id, "p_color": color,
            }).execute().data
        return _response(data)
    except APIError as error:
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "42501":
            raise PermissionDenied from None
        if error.code in ("22023", "22P02"):
            raise InvalidProposal() from None
        raise ProposalUnavailable() from None
    except Exception:
        raise ProposalUnavailable() from None


@sensitive_variables()
def scores(access_token, *, plan_id):
    """Obtiene victorias por propuesta con el JWT del integrante."""
    if not access_token:
        raise SessionExpired()
    plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("uno_proposal_scores", {"p_plan_id": plan_id}).execute().data
        if not isinstance(data, list):
            raise ValueError
        result = {}
        for row in data:
            proposal_id, victories = row["proposal_id"], row["victories"]
            if (type(proposal_id) is not int or proposal_id < 1 or proposal_id in result
                    or type(victories) is not int or victories < 0):
                raise ValueError
            result[proposal_id] = victories
        return result
    except APIError as error:
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "42501":
            raise PermissionDenied from None
        raise ProposalUnavailable() from None
    except Exception:
        raise ProposalUnavailable() from None
