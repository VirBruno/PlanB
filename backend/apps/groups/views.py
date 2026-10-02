from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse
from django.shortcuts import redirect, render
from itertools import groupby
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from apps.plans.services import plan_service
from apps.plans.services.exceptions import PlanUnavailable
from .forms import GroupForm
from .services import group_service
from .services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def create(request):
    form = GroupForm(request.POST if request.method == "POST" else None)
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            group_id = group_service.create_group(
                session_service.get_access_token(request),
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
            )
        except InvalidGroup as error:
            form.add_error(None, str(error))
        except GroupUnavailable:
            form.add_error(None, "No pudimos confirmar la creación. Revisá Mis grupos antes de volver a enviar el formulario.")
            status = 503
        else:
            messages.success(request, "¡Tu grupo está listo!")
            return redirect("groups:detail", group_id=group_id)
    return render(request, "groups/create.html", {
        "form": form, "planb_user": request.planb_user,
    }, status=status)


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def detail(request, group_id):
    try:
        access_token = session_service.get_access_token(request)
        group = group_service.get_group(access_token, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return render(request, "groups/detail.html", {
            "unavailable": True, "planb_user": request.planb_user,
        }, status=503)
    try:
        plans_page = plan_service.list_plans(access_token, page=1, group_id=str(group_id))
        all_plans = list(plans_page["items"])
        while plans_page.get("next_page"):
            plans_page = plan_service.list_plans(
                access_token, page=plans_page["next_page"], group_id=str(group_id),
            )
            all_plans.extend(plans_page["items"])
    except PlanUnavailable:
        return render(request, "groups/detail.html", {
            "group": group, "plans_unavailable": True,
            "planb_user": request.planb_user,
        }, status=503)
    plans_by_date = [
        {"date": date, "items": list(items)}
        for date, items in groupby(all_plans, key=lambda plan: plan["created_at"].date())
    ]
    return render(request, "groups/detail.html", {
        "group": group, "plans_page": {"items": all_plans},
        "plans_by_date": plans_by_date, "planb_user": request.planb_user,
    })

def _owned_group(request, group_id):
    group = group_service.get_group(session_service.get_access_token(request), group_id)
    if group["role"] != "owner":
        raise PermissionDenied("Sólo el owner puede modificar o eliminar este grupo.")
    return group


def _management_unavailable(request, group_id):
    return render(request, "groups/detail.html", {
        "unavailable": True, "planb_user": request.planb_user,
        "retry_url": reverse("groups:detail", args=[group_id]),
    }, status=503)


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def edit(request, group_id):
    try:
        group = _owned_group(request, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return _management_unavailable(request, group_id)
    form = GroupForm(
        request.POST if request.method == "POST" else None,
        initial={"name": group["name"], "description": group["description"] or ""},
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            group_service.update_group(
                session_service.get_access_token(request), group_id,
                name=form.cleaned_data["name"], description=form.cleaned_data["description"],
            )
        except GroupNotFound:
            # Puede desaparecer o cambiar el owner desde que se leyó el detalle.
            raise Http404("No encontramos ese grupo.") from None
        except InvalidGroup as error:
            form.add_error(None, str(error))
        except GroupUnavailable:
            form.add_error(None, "No pudimos confirmar los cambios. Revisá el detalle del grupo antes de volver a guardar.")
            status = 503
        else:
            messages.success(request, "Los cambios del grupo se guardaron.")
            return redirect("groups:detail", group_id=group_id)
    return render(request, "groups/edit.html", {
        "form": form, "group": group, "planb_user": request.planb_user,
    }, status=status)


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def delete_confirmation(request, group_id):
    try:
        group = _owned_group(request, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return _management_unavailable(request, group_id)
    return render(request, "groups/delete_confirmation.html", {
        "group": group, "planb_user": request.planb_user,
    })


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def delete(request, group_id):
    try:
        group = _owned_group(request, group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return _management_unavailable(request, group_id)
    try:
        group_service.delete_group(session_service.get_access_token(request), group_id)
    except GroupNotFound:
        raise Http404("No encontramos ese grupo.") from None
    except (GroupUnavailable, InvalidGroup):
        return render(request, "groups/delete_confirmation.html", {
            "group": group, "planb_user": request.planb_user,
            "error": "No pudimos confirmar la eliminación. Revisá Mis grupos antes de volver a intentarlo.",
        }, status=503)
    messages.success(request, "El grupo se eliminó.")
    return redirect("users:dashboard")
