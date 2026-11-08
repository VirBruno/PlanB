"""Inputs compartidos por Gemini y el fingerprint; sin usuarios ni configuración privada."""
import hashlib
import json
import unicodedata

from .place_types import Location


def _text(value, limit):
    return " ".join(unicodedata.normalize("NFC", value or "").split())[:limit]


def activity_payload(proposal):
    return {"title": _text(proposal.get("tittle"), 255),
            "description": _text(proposal.get("description"), 4000)}


def input_fingerprint(plan, proposal):
    try:
        location = Location(proposal.get("latitude"), proposal.get("longitude"))
        # 1 y 1.0, así como -0.0 y 0.0, representan la misma coordenada.
        position = {"latitude": float(location.latitude) or 0.0,
                    "longitude": float(location.longitude) or 0.0}
    except ValueError:
        position = {"location_text": _text(proposal.get("location_text"), 500)}
    payload = {"version": 1, "proposal_id": str(plan["selected_proposal_id"]),
               "activity": activity_payload(proposal), "position": position}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
