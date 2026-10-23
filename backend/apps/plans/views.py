import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, time

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from apps.groups.services import group_service
from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import profile_service, session_service
from apps.users.services.exceptions import ServiceUnavailable
from .forms import PlanForm
from .forms import ProposalForm
from .services import plan_service
from .services.exceptions import InvalidPlan, PlanNotFound, PlanUnavailable, PlanningSchemaUnavailable
from .services import proposal_service
from .services import election_service
from .services import plan_confirmation_service
from .services import weather_service
from .services import plan_weather_service
from .services.proposal_exceptions import (
    ElectionFlexibilityUnavailable, ElectionSchemaUnavailable, InvalidProposal,
    ProposalAlreadyExists, ProposalNotFound, ProposalUnavailable,
)

IDEAL_AREA_RADIUS_METERS = 200


def _unavailable(request, *, retry_url=None):
    return render(request, "plans/unavailable.html", {
        "planb_user": request.planb_user,
        "retry_url": retry_url or request.path,
    }, status=503)


def _plan_and_group(request, plan_id):
    token = session_service.get_access_token(request)
    plan = plan_service.get_plan(token, plan_id)
    if not plan["group_id"]:
        raise PlanNotFound()
    group = group_service.get_group(token, plan["group_id"])
    return plan, group


def _require_group_admin(group):
    if group["role"] != "owner":
        raise PermissionDenied("Sólo el admin del grupo puede gestionar sus planes.")


def _proposal_ideal_summary(proposals):
    points = sorted({
        (float(proposal["longitude"]), float(proposal["latitude"]))
        for proposal in proposals
    })
    if not points:
        return None

    budget_estimates = []
    for proposal in proposals:
        minimum = proposal.get("budget_min")
        maximum = proposal.get("budget_max")
        if minimum is not None or maximum is not None:
            minimum = Decimal(str(minimum)) if minimum is not None else Decimal(str(maximum))
            maximum = Decimal(str(maximum)) if maximum is not None else minimum
            budget_estimates.append((minimum + maximum) / 2)

    ideal_budget = None
    if budget_estimates:
        ideal_budget = (sum(budget_estimates) / len(budget_estimates)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        )

    return {
        "points": [[latitude, longitude] for longitude, latitude in points],
        "center": [
            sum(latitude for _, latitude in points) / len(points),
            sum(longitude for longitude, _ in points) / len(points),
        ],
        "radius_meters": IDEAL_AREA_RADIUS_METERS,
        "ideal_budget": ideal_budget,
        "budget_count": len(budget_estimates),
    }


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def index(request):
    try:
        page = int(request.GET.get("page", "1"))
    except (ValueError, TypeError):
        page = 1
    try:
        plans_page = plan_service.list_plans(session_service.get_access_token(request), page=page)
    except PlanUnavailable:
        return _unavailable(request)
    return render(request, "plans/index.html", {
        "plans_page": plans_page, "planb_user": request.planb_user,
    })


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def group_index(request, group_id):
    token = session_service.get_access_token(request)
    try:
        group_service.get_group(token, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return _unavailable(request)
    return redirect("groups:detail", group_id=group_id)



def _plan_weather_data(form):
    """Datos opcionales del plan; la ubicación se valida contra la API geográfica."""
    scheduled_at = form.cleaned_data["scheduled_at"]
    if scheduled_at is None:
        return {"scheduled_at": None, "weather_province": None,
                "weather_city_id": None, "weather_city_name": None}
    province = form.cleaned_data["weather_province"]
    city_id = form.cleaned_data["weather_city_id"]
    city = plan_weather_service.resolve_city(province, city_id)
    return {"scheduled_at": scheduled_at, "weather_province": province,
            "weather_city_id": city_id, "weather_city_name": city["name"]}


def _group_weather_access(request, group_id):
    """Sólo miembros autenticados del grupo pueden consultar la geocodificación."""
    token = session_service.get_access_token(request)
    return group_service.get_group(token, group_id)


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def group_weather_cities(request, group_id):
    try:
        _group_weather_access(request, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar el grupo."}, status=503)
    try:
        cities = plan_weather_service.search_cities(request.GET.get("provincia", ""), request.GET.get("q", ""))
    except ValueError as error:
        return JsonResponse({"status": "invalid", "message": str(error)}, status=400)
    except weather_service.WeatherUnavailable:
        return JsonResponse({"status": "unavailable", "message": "No pudimos buscar localidades."}, status=503)
    return JsonResponse({"status": "ok", "cities": cities})


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def group_weather_at(request, group_id):
    try:
        _group_weather_access(request, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar el grupo."}, status=503)
    try:
        city_id = int(request.GET.get("localidad_id", ""))
        chosen_date = date.fromisoformat(request.GET.get("fecha", ""))
        chosen_time = time.fromisoformat(request.GET.get("hora", ""))
        if chosen_time.tzinfo is not None or chosen_time.second or chosen_time.microsecond:
            raise ValueError
        result = plan_weather_service.forecast_for_datetime(
            request.GET.get("provincia", ""), city_id, chosen_date, chosen_time)
    except (ValueError, TypeError):
        return JsonResponse({"status": "invalid", "message": "Elegí provincia, localidad, fecha y horario válidos."}, status=400)
    except weather_service.WeatherUnavailable:
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar el pronóstico."}, status=503)
    return JsonResponse(result)


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def create(request, group_id):
    token = session_service.get_access_token(request)
    try:
        group = group_service.get_group(token, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return _unavailable(request)
    form = PlanForm(request.POST if request.method == "POST" else None)
    status_code = 200
    if request.method == "POST" and form.is_valid():
        try:
            planning = _plan_weather_data(form)
            plan_id = plan_service.create_plan(
                token, created_by=request.planb_user["id"], group_id=str(group_id),
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                status=form.cleaned_data["status"],
                **(planning if planning["scheduled_at"] is not None else {}),
            )
        except ValueError as error:
            form.add_error("weather_city_id", str(error))
        except weather_service.WeatherUnavailable:
            form.add_error("weather_city_id", "No pudimos validar la localidad. Intentá nuevamente.")
        except PlanningSchemaUnavailable as error:
            form.add_error(None, str(error))
        except InvalidPlan as error:
            form.add_error(None, str(error))
        except PlanUnavailable:
            form.add_error(None, "No pudimos confirmar la creación. Revisá los planes del grupo antes de volver a enviarlo.")
            status_code = 503
        else:
            messages.success(request, "El plan se creó.")
            return redirect("groups:detail", group_id=group_id)
    return render(request, "plans/form.html", {
        "form": form, "group": group, "is_edit": False,
        "planb_user": request.planb_user,
    }, status=status_code)


@private_page(referrer_policy="strict-origin-when-cross-origin")
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def detail(request, plan_id):
    token = session_service.get_access_token(request)
    try:
        plan = plan_service.get_plan(token, plan_id)
        if not plan["group_id"]:
            raise PlanNotFound()
        group = group_service.get_group(token, plan["group_id"])
        proposals = proposal_service.list_proposals(token, plan_id)
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except GroupNotFound:
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable, ProposalUnavailable):
        return _unavailable(request)
    try:
        usernames = profile_service.usernames_for_ids(
            proposal["created_by"] for proposal in proposals
        )
    except ServiceUnavailable:
        usernames = {}
    for proposal in proposals:
        proposal["can_manage"] = proposal["created_by"] == request.planb_user["id"]
        proposal["creator_username"] = usernames.get(proposal["created_by"], "Usuario")
    my_proposal = next(
        (proposal for proposal in proposals if proposal["can_manage"]), None,
    )
    election_method = plan.get("election_method")
    voted_proposal_id = None
    if election_method == "votes":
        try:
            voted_proposal_id = election_service.voted_proposal(
                token, plan_id=plan_id,
            )
        except ProposalUnavailable:
            return _unavailable(request)
    leaderboard = []
    champion_id = None
    if election_method in ("votes", "combat"):
        score_key = "votes" if election_method == "votes" else "Score"
        leaderboard = sorted(
            proposals, key=lambda proposal: (-proposal[score_key], proposal["id"]),
        )
        if leaderboard and leaderboard[0][score_key] > 0 and (
            len(leaderboard) == 1 or leaderboard[0][score_key] > leaderboard[1][score_key]
        ):
            champion_id = leaderboard[0]["id"]
    confirmed_proposal = next(
        (proposal for proposal in proposals if proposal["id"] == plan.get("selected_proposal_id")),
        None,
    )
    for proposal in proposals:
        proposal["is_confirmed"] = proposal is confirmed_proposal
        proposal["can_confirm"] = (
            group["role"] == "owner" and plan.get("decision_schema", True) and not plan.get("selected_proposal_id")
            and proposal["date_pick"] is not None
            and (election_method not in ("votes", "combat") or proposal["id"] == champion_id)
        )
    # El cálculo es de consulta: cualquier integrante con acceso al plan puede verlo.
    # _plan_and_group/get_group ya valida la pertenencia mediante el JWT del usuario.
    show_ideal = request.GET.get("ideal") == "1"
    scheduled_local = (timezone.localtime(plan["scheduled_at"], weather_service.ARGENTINA_TZ)
                       if plan.get("scheduled_at") else None)
    return render(request, "plans/detail.html", {
        "plan": plan, "group": group, "can_manage": group["role"] == "owner",
        "election_method": election_method,
        "confirmed_proposal": confirmed_proposal,
        "decision_schema": plan.get("decision_schema", False),
        "can_create_proposal": not any(
            proposal["created_by"] == request.planb_user["id"] for proposal in proposals
        ),
        "proposals": proposals,
        "my_proposal": my_proposal,
        "leaderboard": leaderboard,
        "champion_id": champion_id,
        "voted_proposal_id": voted_proposal_id,
        "ideal_summary": _proposal_ideal_summary(proposals) if show_ideal else None,
        "show_ideal_popup": show_ideal and election_method in (None, "ideal"),
        "scheduled_local": scheduled_local,
        "scheduled_date_iso": scheduled_local.strftime("%Y-%m-%d") if scheduled_local else "",
        "scheduled_hour": scheduled_local.strftime("%H:%M") if scheduled_local else "",
        "scheduled_display": scheduled_local.strftime("%d/%m/%Y · %H:%M") if scheduled_local else "",
        "plan_province_name": plan_weather_service.PROVINCES.get(plan.get("weather_province"), ""),
        "planb_user": request.planb_user,
    })


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def proposal_weather(request, plan_id, proposal_id):
    """Clima de una propuesta verificada dentro del plan/grupo del usuario."""
    token = session_service.get_access_token(request)
    try:
        plan = plan_service.get_plan(token, plan_id)
        if not plan["group_id"]:
            raise PlanNotFound()
        # El acceso al grupo se comprueba con el JWT de la sesión, igual que en detail.
        group_service.get_group(token, plan["group_id"])
        proposals = proposal_service.list_proposals(token, plan_id)
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable, ProposalUnavailable):
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar este plan."}, status=503)

    proposal = next((item for item in proposals if item["id"] == proposal_id), None)
    if proposal is None:
        raise Http404("No encontramos esa propuesta.")
    try:
        result = weather_service.forecast_for_proposal(proposal)
    except weather_service.WeatherUnavailable:
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar el pronóstico ahora."}, status=503)
    response = JsonResponse(result)
    return response


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def plan_weather_cities(request, plan_id):
    """Búsqueda geográfica privada: solo para miembros con acceso al plan."""
    try:
        _plan_and_group(request, plan_id)
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar este plan."}, status=503)
    try:
        cities = plan_weather_service.search_cities(
            request.GET.get("provincia", ""), request.GET.get("q", ""),
        )
    except ValueError as error:
        return JsonResponse({"status": "invalid", "message": str(error)}, status=400)
    except weather_service.WeatherUnavailable:
        return JsonResponse({"status": "unavailable", "message": "No pudimos buscar localidades en este momento."}, status=503)
    return JsonResponse({"status": "ok", "cities": cities})


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def plan_weather_forecast(request, plan_id):
    """Pronóstico de una localidad de Argentina, validada por la API geográfica."""
    try:
        _plan_and_group(request, plan_id)
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar este plan."}, status=503)
    try:
        city_id = int(request.GET.get("localidad_id", ""))
        result = plan_weather_service.forecast_for_city(request.GET.get("provincia", ""), city_id)
    except (ValueError, TypeError):
        return JsonResponse({"status": "invalid", "message": "Seleccioná una localidad válida de la provincia elegida."}, status=400)
    except weather_service.WeatherUnavailable:
        return JsonResponse({"status": "unavailable", "message": "No pudimos consultar el pronóstico. Intentá nuevamente."}, status=503)
    return JsonResponse(result)


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_ideal(request, plan_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    # _proposal_plan verifica que la persona autenticada puede acceder al plan y
    # a su grupo. No se requieren permisos de owner para este cálculo de lectura.
    return redirect(f"{reverse('plans:detail', args=[plan_id])}?ideal=1#proposal-ideal-card")


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_election_method(request, plan_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    _, group = plan_group
    _require_group_admin(group)
    method = request.POST.get("method")
    try:
        election_service.set_method(
            session_service.get_access_token(request), plan_id=plan_id, method=method,
        )
    except InvalidProposal:
        messages.error(request, "El método de elección no es válido.")
    except ElectionFlexibilityUnavailable:
        messages.error(request, "Para cambiar el método después de tener resultados, aplicá 202610100002_flexible_proposal_elections.sql en Supabase.")
        return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposal-election")
    except ElectionSchemaUnavailable:
        if method == "ideal":
            messages.info(request, "Se abrió el cálculo ideal; aplicá la migración para guardar el método.")
            return redirect(
                f"{reverse('plans:detail', args=[plan_id])}?ideal=1#proposal-ideal-card",
            )
        messages.error(request, "Para usar Votación o Combate, aplicá primero la migración de métodos de elección en Supabase.")
        return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposal-election")
    except ProposalUnavailable:
        if method == "ideal":
            messages.info(request, "Se abrió el cálculo ideal; el método todavía no pudo guardarse.")
            return redirect(
                f"{reverse('plans:detail', args=[plan_id])}?ideal=1#proposal-ideal-card",
            )
        return _unavailable(request)
    else:
        messages.success(request, "Se actualizó el método de elección.")
        if method == "ideal":
            return redirect(
                f"{reverse('plans:detail', args=[plan_id])}?ideal=1#proposal-ideal-card",
            )
    return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposal-election")


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_vote(request, plan_id, proposal_id):
    if _proposal_plan(request, plan_id) is None:
        return _unavailable(request)
    try:
        election_service.cast_vote(
            session_service.get_access_token(request),
            plan_id=plan_id, proposal_id=proposal_id,
        )
    except ElectionFlexibilityUnavailable:
        messages.error(request, "Para cambiar un voto existente, aplicá 202610100002_flexible_proposal_elections.sql en Supabase.")
    except ElectionSchemaUnavailable:
        messages.error(request, "La votación requiere aplicar la migración de métodos de elección en Supabase.")
    except (InvalidProposal, ProposalNotFound):
        messages.error(request, "No se pudo registrar ese voto.")
    except ProposalUnavailable:
        return _unavailable(request)
    else:
        messages.success(request, "Tu voto quedó registrado.")
    return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposals-title")


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_vote_remove(request, plan_id):
    if _proposal_plan(request, plan_id) is None:
        return _unavailable(request)
    try:
        removed_proposal_id = election_service.remove_vote(
            session_service.get_access_token(request), plan_id=plan_id,
        )
    except ElectionFlexibilityUnavailable:
        messages.error(request, "Para eliminar un voto existente, aplicá 202610100002_flexible_proposal_elections.sql en Supabase.")
    except ElectionSchemaUnavailable:
        messages.error(request, "La votación requiere aplicar la migración de métodos de elección en Supabase.")
    except InvalidProposal:
        messages.error(request, "No se pudo eliminar el voto.")
    except ProposalUnavailable:
        return _unavailable(request)
    else:
        if removed_proposal_id is None:
            messages.info(request, "No tenés un voto registrado para eliminar.")
        else:
            messages.success(request, "Se eliminó tu voto.")
    return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposals-title")


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_score(request, plan_id, proposal_id):
    if _proposal_plan(request, plan_id) is None:
        return _unavailable(request)
    try:
        score = int(request.POST.get("score", ""))
    except (ValueError, TypeError):
        score = -1
    try:
        election_service.save_score(
            session_service.get_access_token(request), plan_id=plan_id,
            proposal_id=proposal_id, score=score,
        )
    except ElectionSchemaUnavailable:
        messages.error(request, "Combate requiere aplicar la migración de métodos de elección en Supabase.")
    except (InvalidProposal, ProposalNotFound):
        messages.error(request, "No se pudo guardar ese puntaje.")
    except ProposalUnavailable:
        return _unavailable(request)
    else:
        messages.success(request, "Se guardó tu mejor puntaje.")
    return redirect(f"{reverse('plans:detail', args=[plan_id])}#proposal-combat")


def _proposal_plan(request, plan_id):
    try:
        return _plan_and_group(request, plan_id)
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return None


def _proposal_form(request, *, plan_id, proposal=None):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    plan, group = plan_group
    if plan.get("selected_proposal_id"):
        raise PermissionDenied("Reabrí la decisión antes de modificar propuestas.")
    initial = {}
    if proposal:
        initial = {
            "tittle": proposal["tittle"], "description": proposal["description"] or "",
            "date_pick": proposal["date_pick"], "type": proposal["type"],
            "position": json.dumps(proposal["posicion"]),
            "budget_min": proposal["budget_min"], "budget_max": proposal["budget_max"],
        }
    form = ProposalForm(request.POST if request.method == "POST" else None, initial=initial)
    status_code = 200
    if request.method == "POST" and form.is_valid():
        date_pick = form.cleaned_data["date_pick"]
        if date_pick and timezone.is_naive(date_pick):
            date_pick = timezone.make_aware(date_pick, timezone.get_current_timezone())
        try:
            if proposal:
                proposal_service.update_proposal(
                    session_service.get_access_token(request), plan_id=plan_id,
                    proposal_id=proposal["id"], tittle=form.cleaned_data["tittle"],
                    description=form.cleaned_data["description"], date_pick=date_pick,
                    proposal_type=form.cleaned_data["type"],
                    position=form.cleaned_data["position"],
                    budget_min=form.cleaned_data["budget_min"],
                    budget_max=form.cleaned_data["budget_max"],
                )
            else:
                proposal_service.create_proposal(
                    session_service.get_access_token(request), plan_id=plan_id,
                    created_by=request.planb_user["id"],
                    tittle=form.cleaned_data["tittle"],
                    description=form.cleaned_data["description"], date_pick=date_pick,
                    proposal_type=form.cleaned_data["type"],
                    position=form.cleaned_data["position"],
                    budget_min=form.cleaned_data["budget_min"],
                    budget_max=form.cleaned_data["budget_max"],
                )
        except ProposalNotFound:
            raise Http404("No encontramos esa propuesta.") from None
        except ProposalAlreadyExists as error:
            form.add_error(None, str(error))
        except InvalidProposal as error:
            form.add_error(None, str(error))
        except ProposalUnavailable:
            form.add_error(None, "No pudimos confirmar los cambios. Revisá las propuestas antes de volver a enviar.")
            status_code = 503
        else:
            messages.success(
                request, "La propuesta se guardó." if proposal else "La propuesta se creó.",
            )
            return redirect("plans:detail", plan_id=plan_id)
    return render(request, "plans/proposal_form.html", {
        "form": form, "plan": plan, "group": group,
        "proposal": proposal, "is_edit": proposal is not None,
        "planb_user": request.planb_user,
    }, status=status_code)


@private_page(referrer_policy="strict-origin-when-cross-origin")
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def proposal_create(request, plan_id):
    if request.method == "GET":
        try:
            already_exists = proposal_service.has_user_proposal(
                session_service.get_access_token(request),
                plan_id=plan_id, created_by=request.planb_user["id"],
            )
        except ProposalUnavailable:
            return _unavailable(request)
        if already_exists:
            messages.info(request, "Ya creaste una propuesta para este plan.")
            return redirect("plans:detail", plan_id=plan_id)
    return _proposal_form(request, plan_id=plan_id)


@private_page(referrer_policy="strict-origin-when-cross-origin")
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def proposal_edit(request, plan_id, proposal_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    try:
        proposal = proposal_service.get_proposal(
            session_service.get_access_token(request),
            plan_id=plan_id, proposal_id=proposal_id,
        )
    except ProposalNotFound:
        raise Http404("No encontramos esa propuesta.") from None
    except ProposalUnavailable:
        return _unavailable(request)
    if proposal["created_by"] != request.planb_user["id"]:
        raise PermissionDenied("Sólo quien creó la propuesta puede modificarla.")
    return _proposal_form(request, plan_id=plan_id, proposal=proposal)


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def proposal_delete(request, plan_id, proposal_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    if plan_group[0].get("selected_proposal_id"):
        raise PermissionDenied("Reabrí la decisión antes de eliminar propuestas.")
    try:
        proposal = proposal_service.get_proposal(
            session_service.get_access_token(request),
            plan_id=plan_id, proposal_id=proposal_id,
        )
        if proposal["created_by"] != request.planb_user["id"]:
            raise PermissionDenied("Sólo quien creó la propuesta puede eliminarla.")
        proposal_service.delete_proposal(
            session_service.get_access_token(request),
            plan_id=plan_id, proposal_id=proposal_id,
        )
    except ProposalNotFound:
        raise Http404("No encontramos esa propuesta.") from None
    except ProposalUnavailable:
        return _unavailable(request)
    messages.success(request, "La propuesta se eliminó.")
    return redirect("plans:detail", plan_id=plan_id)


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def edit(request, plan_id):
    try:
        plan, group = _plan_and_group(request, plan_id)
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except GroupNotFound:
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return _unavailable(request)
    _require_group_admin(group)
    token = session_service.get_access_token(request)
    form = PlanForm(
        request.POST if request.method == "POST" else None,
        initial={
            "name": plan["name"], "description": plan["description"],
            "status": "true" if plan["status"] else "false",
            "plan_date": (timezone.localtime(plan["scheduled_at"], weather_service.ARGENTINA_TZ).date()
                          if plan.get("scheduled_at") else None),
            "plan_time": (timezone.localtime(plan["scheduled_at"], weather_service.ARGENTINA_TZ).time().replace(tzinfo=None)
                          if plan.get("scheduled_at") else None),
            "weather_province": plan.get("weather_province"),
            "weather_city_id": plan.get("weather_city_id"),
        },
    )
    if plan.get("selected_proposal_id"):
        for field in ("plan_date", "plan_time", "weather_province", "weather_city_id"):
            form.fields[field].disabled = True
            form.initial[field] = None
    status_code = 200
    if request.method == "POST" and form.is_valid():
        try:
            planning = _plan_weather_data(form) if not plan.get("selected_proposal_id") else {}
            plan_service.update_plan(
                token, plan_id=plan_id,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                status=form.cleaned_data["status"],
                **({"update_planning": True, **planning} if not plan.get("selected_proposal_id")
                   and (plan.get("planning_schema") or planning.get("scheduled_at")) else {}),
            )
        except ValueError as error:
            form.add_error("weather_city_id", str(error))
        except weather_service.WeatherUnavailable:
            form.add_error("weather_city_id", "No pudimos validar la localidad. Intentá nuevamente.")
        except PlanningSchemaUnavailable as error:
            form.add_error(None, str(error))
        except PlanNotFound:
            raise Http404("No encontramos ese plan.") from None
        except GroupNotFound:
            raise Http404("No encontramos ese plan.") from None
        except InvalidPlan as error:
            form.add_error(None, str(error))
        except PlanUnavailable:
            form.add_error(None, "No pudimos confirmar los cambios. Revisá el plan antes de volver a guardar.")
            status_code = 503
        else:
            messages.success(request, "Los cambios del plan se guardaron.")
            return redirect("plans:detail", plan_id=plan_id)
    return render(request, "plans/form.html", {
        "form": form, "plan": plan, "group": group, "is_edit": True,
        "selected_city_name": plan.get("weather_city_name", "") if plan else "",
        "confirmed_plan": bool(plan.get("selected_proposal_id")),
        "planb_user": request.planb_user,
    }, status=status_code)


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def delete_confirmation(request, plan_id):
    try:
        plan, group = _plan_and_group(request, plan_id)
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except GroupNotFound:
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return _unavailable(request)
    _require_group_admin(group)
    return render(request, "plans/delete_confirmation.html", {
        "plan": plan, "group": group, "planb_user": request.planb_user,
    })


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def delete(request, plan_id):
    try:
        plan, group = _plan_and_group(request, plan_id)
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except GroupNotFound:
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return _unavailable(request)
    _require_group_admin(group)
    try:
        plan_service.delete_plan(session_service.get_access_token(request), str(plan_id))
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except PlanUnavailable:
        return _unavailable(
            request, retry_url=reverse("plans:delete_confirmation", args=[plan_id]),
        )
    messages.success(request, "El plan se eliminó.")
    return redirect("groups:detail", group_id=group["id"])


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def confirm_plan_proposal(request, plan_id, proposal_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    plan, group = plan_group
    _require_group_admin(group)
    if plan.get("selected_proposal_id"):
        messages.error(request, "El encuentro ya está confirmado. Reabrilo antes de cambiarlo.")
        return redirect("plans:detail", plan_id=plan_id)
    try:
        plan_confirmation_service.confirm(
            session_service.get_access_token(request), plan_id=plan_id,
            proposal_id=proposal_id,
        )
    except InvalidPlan as error:
        messages.error(request, str(error))
    except PlanUnavailable:
        return _unavailable(request)
    else:
        messages.success(request, "¡Encuentro confirmado! Fecha y ubicación elegidas desde la propuesta.")
    return redirect("plans:detail", plan_id=plan_id)


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def reopen_plan_decision(request, plan_id):
    plan_group = _proposal_plan(request, plan_id)
    if plan_group is None:
        return _unavailable(request)
    _, group = plan_group
    _require_group_admin(group)
    try:
        plan_confirmation_service.reopen(session_service.get_access_token(request), plan_id=plan_id)
    except InvalidPlan as error:
        messages.error(request, str(error))
    except PlanUnavailable:
        return _unavailable(request)
    else:
        messages.success(request, "El plan volvió a recibir propuestas; ya no tiene encuentro confirmado.")
    return redirect("plans:detail", plan_id=plan_id)
