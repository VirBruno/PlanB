"""Lecturas con JWT/RLS; escrituras sólo por RPC backend, sin payload del navegador."""
from math import isfinite
import re
from uuid import UUID

from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .place_types import Location, RecommendationError

COLUMNS = ("plan_id,proposal_id,confirmed_at,status,error_code,items,base_location,"
           "criteria,created_at,updated_at,generated_at")


def _translate(error):
    if isinstance(error, APIError) and error.code in ("PGRST301", "PGRST303"):
        raise SessionExpired() from None
    raise RecommendationError("storage_unavailable") from None


def _validate_row(row, plan):
    if (not isinstance(row, dict) or row.get("plan_id") != plan["id"]
            or row.get("proposal_id") != plan["selected_proposal_id"]
            or row.get("status") not in ("pending", "ready", "failed")
            or not isinstance(row.get("items"), list) or len(row["items"]) > 5):
        raise ValueError
    seen = set()
    for rank, item in enumerate(row["items"], 1):
        if not isinstance(item, dict):
            raise ValueError
        Location(item.get("latitude"), item.get("longitude"))
        identifier = item.get("provider_place_id")
        distance = item.get("distance_meters")
        if (not isinstance(identifier, str) or not identifier or identifier in seen
                or item.get("provider") != "geoapify" or item.get("rank") != rank
                or not isinstance(item.get("name"), str) or not item["name"]
                or not isinstance(item.get("categories"), list)
                or any(not isinstance(c, str) for c in item["categories"])
                or isinstance(distance, bool) or not isinstance(distance, (int, float))
                or not isfinite(distance) or distance < 0):
            raise ValueError
        seen.add(identifier)
    return row


@sensitive_variables()
def read(token, plan):
    try:
        with clients.public_client(access_token=token) as client:
            rows = (client.table("plan_recommendations").select(COLUMNS)
                    .eq("plan_id", plan["id"]).eq("proposal_id", plan["selected_proposal_id"])
                    .eq("confirmed_at", plan["confirmed_at"].isoformat()).limit(1).execute().data)
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError
        return _validate_row(rows[0], plan) if rows else None
    except Exception as exc:
        _translate(exc)


@sensitive_variables()
def _write(function, payload):
    # Única excepción de dominio al JWT: no otorgar a authenticated una RPC que
    # acepta snapshots arbitrarios. service_role sólo invoca estas dos funciones.
    try:
        with clients.administrative_client() as client:
            return client.rpc(function, payload).execute().data
    except Exception as exc:
        _translate(exc)


def claim(plan, *, replace=False, input_fingerprint=None):
    payload = {
        "p_plan_id": plan["id"], "p_proposal_id": plan["selected_proposal_id"],
        "p_confirmed_at": plan["confirmed_at"].isoformat(), "p_replace": replace,
    }
    if input_fingerprint is not None:
        if not isinstance(input_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", input_fingerprint):
            raise ValueError("Fingerprint inválido")
        payload["p_input_fingerprint"] = input_fingerprint
    ticket = _write("claim_plan_recommendations", payload)
    if ticket is not None:
        try:
            return str(UUID(ticket))
        except (TypeError, ValueError, AttributeError):
            raise RecommendationError("storage_unavailable") from None
    return None


def finish(plan, generation_id, *, items, error_code=None, base_location=None, criteria=None):
    result = _write("finish_plan_recommendations", {
        "p_plan_id": plan["id"], "p_generation_id": generation_id,
        "p_items": items, "p_error_code": error_code,
        "p_base_location": base_location, "p_criteria": criteria,
    })
    if type(result) is not bool:
        raise RecommendationError("storage_unavailable")
    return result
