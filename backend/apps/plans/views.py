from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from apps.groups.services import group_service
from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from .forms import PlanForm
from .services import plan_service
from .services.exceptions import InvalidPlan, PlanNotFound, PlanUnavailable


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
    _require_group_admin(group)
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


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def detail(request, plan_id):
    try:
        plan = plan_service.get_plan(session_service.get_access_token(request), plan_id)
        if not plan["group_id"]:
            raise PlanNotFound()
        group = group_service.get_group(session_service.get_access_token(request), plan["group_id"])
    except PlanNotFound:
        raise Http404("No encontramos ese plan.") from None
    except GroupNotFound:
        raise Http404("No encontramos ese plan.") from None
    except (GroupUnavailable, InvalidGroup, PlanUnavailable):
        return _unavailable(request)
    return render(request, "plans/detail.html", {
        "plan": plan, "group": group, "can_manage": group["role"] == "owner",
        "planb_user": request.planb_user,
    })


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