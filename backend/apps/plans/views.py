import json
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
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
from .services.exceptions import InvalidPlan, PlanNotFound, PlanUnavailable
from .services import proposal_service
from .services.proposal_exceptions import (
    InvalidProposal, ProposalAlreadyExists, ProposalNotFound, ProposalUnavailable,
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
            plan_id = plan_service.create_plan(
                token, created_by=request.planb_user["id"], group_id=str(group_id),
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                status=form.cleaned_data["status"],
            )
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
    # El cálculo es de consulta: cualquier integrante con acceso al plan puede verlo.
    # _plan_and_group/get_group ya valida la pertenencia mediante el JWT del usuario.
    show_ideal = request.GET.get("ideal") == "1"
    return render(request, "plans/detail.html", {
        "plan": plan, "group": group, "can_manage": group["role"] == "owner",
        "can_create_proposal": not any(
            proposal["created_by"] == request.planb_user["id"] for proposal in proposals
        ),
        "proposals": proposals,
        "ideal_summary": _proposal_ideal_summary(proposals) if show_ideal else None,
        "planb_user": request.planb_user,
    })


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
    if _proposal_plan(request, plan_id) is None:
        return _unavailable(request)
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
        },
    )
    status_code = 200
    if request.method == "POST" and form.is_valid():
        try:
            plan_service.update_plan(
                token, plan_id=plan_id,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                status=form.cleaned_data["status"],
            )
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
