"""Orquestación independiente de confirmación; ninguna lectura genera lugares."""
from dataclasses import asdict

from django.views.decorators.debug import sensitive_variables

from apps.groups.services import group_service
from . import place_intent_service, plan_service, proposal_service, recommendation_store
from .exceptions import PlanNotFound
from .gemini_intent_service import GeminiIntentClient
from .geoapify_service import GeoapifyClient
from .place_ranking_service import google_maps_url, google_search_url, rank_candidates
from .place_types import PlaceSearch, RecommendationError
from .proposal_exceptions import ProposalNotFound, ProposalUnavailable
from .recommendation_inputs import input_fingerprint


def _authorized_plan(token, plan_id):
    plan = plan_service.get_plan(token, plan_id)
    if not plan.get("group_id"):
        raise PlanNotFound()
    group_service.get_group(token, plan["group_id"])
    return plan


def _confirmed(plan):
    return (plan.get("status") is True and bool(plan.get("selected_proposal_id"))
            and plan.get("confirmed_at") is not None)


def _criteria(row):
    value = row.get("criteria") if row else None
    return value if isinstance(value, dict) else {}


def _proposal(token, plan):
    try:
        return proposal_service.get_proposal(
            token, plan_id=plan["id"], proposal_id=plan["selected_proposal_id"])
    except (ProposalNotFound, ProposalUnavailable):
        return None


def _snapshot(row, fingerprint=None):
    if row is None:
        return {"status": "not_generated", "items": [], "error_code": None}
    result = {key: row.get(key) for key in (
        "status", "error_code", "generated_at", "updated_at",
    )}
    # Lista explícita también en la lectura. Nunca exponer token de generación,
    # datos crudos ni futuros campos internos agregados en la base.
    fields = ("provider", "provider_place_id", "name", "categories", "address", "latitude",
              "longitude", "distance_meters", "website", "opening_hours", "media", "rank")
    result["items"] = [{key: item.get(key) for key in fields} for item in row["items"]]
    for item in result["items"]:
        item["google_maps_url"] = google_maps_url(item)
        item["google_search_url"] = google_search_url(item)
    result["result_code"] = "no_results" if row["status"] == "ready" and not row["items"] else None
    criteria = _criteria(row)
    result["has_snapshot"] = bool(row.get("generated_at") or row["items"] or row["status"] == "ready")
    result["ai_summary"] = (place_intent_service.sanitize_summary(criteria.get("summary"))
                            if result["has_snapshot"] else None)
    previous = criteria.get("input_fingerprint")
    attempted = criteria.get("attempted_input_fingerprint")
    result["is_stale"] = bool(previous and fingerprint and previous != fingerprint)
    result["can_update"] = bool(fingerprint and row["status"] != "pending"
                                and (previous or attempted) and previous != fingerprint
                                and attempted != fingerprint)
    result["can_refresh"] = row["status"] == "ready" and not previous
    return result


@sensitive_variables()
def get_recommendations(token, plan_id):
    plan = _authorized_plan(token, plan_id)
    if not _confirmed(plan):
        return {"status": "not_confirmed", "items": [], "error_code": None}
    row = recommendation_store.read(token, plan)
    criteria = _criteria(row)
    # Legacy no dispara llamadas a proveedores ni requiere leer contenido para validarse.
    proposal = _proposal(token, plan) if (criteria.get("input_fingerprint")
                                         or criteria.get("attempted_input_fingerprint")) else None
    return _snapshot(row, input_fingerprint(plan, proposal) if proposal is not None else None)


@sensitive_variables()
def generate_recommendations(token, plan_id, *, replace=False):
    """Idempotente por decisión/inputs. replace sólo expresa una acción explícita.

    No reintenta fallos/pending al recargar, ni utiliza datos de ubicación del cliente.
    La reserva y el reemplazo son transacciones cortas; la red queda fuera de DB.
    """
    plan = _authorized_plan(token, plan_id)
    if not _confirmed(plan):
        return {"status": "not_confirmed", "items": [], "error_code": None}
    row = recommendation_store.read(token, plan)
    if row is not None and not replace and (row["status"] == "pending" or not (
            _criteria(row).get("input_fingerprint") or _criteria(row).get("attempted_input_fingerprint"))):
        return _snapshot(row)
    proposal = _proposal(token, plan)
    fingerprint = input_fingerprint(plan, proposal) if proposal is not None else None
    if row is not None and not replace:
        snapshot = _snapshot(row, fingerprint)
        if not snapshot["can_update"]:
            return snapshot
    # Una lectura fallida no debe destruir ni reemplazar datos útiles.
    if proposal is None and row is not None:
        return _snapshot(row)
    generation_id = recommendation_store.claim(plan, replace=replace, input_fingerprint=fingerprint)
    if generation_id is None:
        return get_recommendations(token, plan_id)
    items, error_code, location, criteria = [], None, None, None
    interpretation = None
    try:
        if proposal is None:
            raise RecommendationError("proposal_unavailable")
        interpretation = GeminiIntentClient().interpret(proposal)
        provider = GeoapifyClient()
        location = place_intent_service.resolve_location(proposal, provider)
        criteria = PlaceSearch(categories=interpretation.categories, location=location)
        items = rank_candidates(provider.search(criteria), criteria)
    except RecommendationError as exc:
        error_code = exc.code
    except (ProposalNotFound, ProposalUnavailable):
        error_code = "proposal_unavailable"
    recommendation_store.finish(
        plan, generation_id, items=items, error_code=error_code,
        base_location=asdict(location) if location else None,
        criteria={**interpretation.criteria(), "input_fingerprint": fingerprint,
                  "categories": list(criteria.categories), "radius_meters": criteria.radius_meters,
                  "candidate_limit": criteria.candidate_limit} if criteria else None,
    )
    # Revalidar acceso/decisión por si cambió mientras consultábamos al proveedor.
    return get_recommendations(token, plan_id)
