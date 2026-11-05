"""Orquestación independiente de confirmación; ninguna lectura genera lugares."""
from dataclasses import asdict

from django.views.decorators.debug import sensitive_variables

from apps.groups.services import group_service
from . import place_intent_service, plan_service, proposal_service, recommendation_store
from .exceptions import PlanNotFound
from .geoapify_service import GeoapifyClient
from .place_ranking_service import google_maps_url, google_search_url, rank_candidates
from .place_types import PlaceSearch, RecommendationError
from .proposal_exceptions import ProposalNotFound, ProposalUnavailable


def _authorized_plan(token, plan_id):
    plan = plan_service.get_plan(token, plan_id)
    if not plan.get("group_id"):
        raise PlanNotFound()
    group_service.get_group(token, plan["group_id"])
    return plan


def _confirmed(plan):
    return (plan.get("status") is True and bool(plan.get("selected_proposal_id"))
            and plan.get("confirmed_at") is not None)


def _snapshot(row):
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
    return result


@sensitive_variables()
def get_recommendations(token, plan_id):
    plan = _authorized_plan(token, plan_id)
    if not _confirmed(plan):
        return {"status": "not_confirmed", "items": [], "error_code": None}
    return _snapshot(recommendation_store.read(token, plan))


@sensitive_variables()
def generate_recommendations(token, plan_id, *, replace=False):
    """Idempotente por decisión. replace es interno para futuros reintentos explícitos.

    No reintenta fallos/pending al recargar, ni utiliza datos de ubicación del cliente.
    La reserva y el reemplazo son transacciones cortas; la red queda fuera de DB.
    """
    plan = _authorized_plan(token, plan_id)
    if not _confirmed(plan):
        return {"status": "not_confirmed", "items": [], "error_code": None}
    row = recommendation_store.read(token, plan)
    if row is not None and not replace:
        return _snapshot(row)
    generation_id = recommendation_store.claim(plan, replace=replace)
    if generation_id is None:
        return get_recommendations(token, plan_id)
    items, error_code, location, criteria = [], None, None, None
    try:
        proposal = proposal_service.get_proposal(
            token, plan_id=plan["id"], proposal_id=plan["selected_proposal_id"])
        categories = place_intent_service.resolve_categories(proposal)
        provider = GeoapifyClient()
        location = place_intent_service.resolve_location(proposal, provider)
        criteria = PlaceSearch(categories=categories, location=location)
        items = rank_candidates(provider.search(criteria), criteria)
    except RecommendationError as exc:
        error_code = exc.code
    except (ProposalNotFound, ProposalUnavailable):
        error_code = "proposal_unavailable"
    recommendation_store.finish(
        plan, generation_id, items=items, error_code=error_code,
        base_location=asdict(location) if location else None,
        criteria={"categories": list(criteria.categories), "radius_meters": criteria.radius_meters,
                  "candidate_limit": criteria.candidate_limit} if criteria else None,
    )
    # Revalidar acceso/decisión por si cambió mientras consultábamos al proveedor.
    return get_recommendations(token, plan_id)
