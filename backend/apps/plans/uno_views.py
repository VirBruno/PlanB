"""Partidas individuales de UNO, guardadas en la sesión de Django."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from apps.plans.services import proposal_service, uno_game, uno_supabase
from apps.plans.services.proposal_exceptions import InvalidProposal, ProposalNotFound, ProposalUnavailable
from apps.plans.services.exceptions import PlanNotFound, PlanUnavailable
from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
def game(request, plan_id, proposal_id):
    # El RPC valida membresía, autoría y estado del plan en una sola transacción.
    # Esta ruta se usa únicamente cuando UNO_SUPABASE_ENABLED está habilitado.
    if getattr(settings, "UNO_SUPABASE_ENABLED", False):
        try:
            result = uno_supabase.game(
                session_service.get_access_token(request), plan_id=plan_id, proposal_id=proposal_id,
                action="status" if request.method == "GET" else request.POST.get("action", ""),
                card_id=request.POST.get("card_id") or None, color=request.POST.get("color") or None,
            )
            return JsonResponse(result)
        except InvalidProposal:
            return JsonResponse({"error": "Esa jugada no está disponible."}, status=400)
        except PermissionDenied:
            return JsonResponse({"error": "Esta partida no está disponible."}, status=403)
        except ProposalUnavailable:
            return JsonResponse({"error": "No pudimos conectar. Probá de nuevo."}, status=503)

    # Las consultas de negocio se hacen con el JWT del usuario y sus RLS.
    from .views import _plan_and_group

    try:
        plan, _group = _plan_and_group(request, plan_id)
        proposal = proposal_service.get_proposal(
            session_service.get_access_token(request), plan_id=plan_id, proposal_id=proposal_id,
        )
    except (PlanNotFound, GroupNotFound, InvalidGroup, ProposalNotFound):
        return JsonResponse({"error": "No encontramos la propuesta."}, status=404)
    except (PlanUnavailable, GroupUnavailable, ProposalUnavailable):
        return JsonResponse({"error": "No pudimos conectar. Probá de nuevo."}, status=503)
    if (str(proposal["created_by"]) != str(request.planb_user["id"])
            or plan.get("election_method") != "combat" or not plan.get("status")
            or plan.get("selected_proposal_id")):
        return JsonResponse({"error": "Esta partida no está disponible."}, status=403)

    key = f"uno:{plan_id}:{proposal_id}"
    state = request.session.get(key)
    if request.method == "GET":
        return JsonResponse({"game": uno_game.public_state(state) if state else None})

    action = request.POST.get("action", "")
    if action == "start":
        state = uno_game.new_game()
    elif not state:
        return JsonResponse({"error": "Primero iniciá una partida."}, status=409)
    else:
        try:
            uno_game.act(state, action, card_id=request.POST.get("card_id"), color=request.POST.get("color"))
        except uno_game.InvalidUnoMove as error:
            return JsonResponse({"error": str(error), "game": uno_game.public_state(state)}, status=400)
    request.session[key] = state
    return JsonResponse({"game": uno_game.public_state(state)})
