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
from .forms import GroupForm, InviteForm, InviteSearchForm
from .services import invitation_service
from .services.invitation_service import (
    InvitationConflict, InvitationNotFound, InvitationUnavailable, InvalidInvitation,
)
from .services import group_service
from .services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup


@private_page
@require_http_methods(["GET"])
@supabase_login_required
@sensitive_variables()
def index(request):
    try:
        page = int(request.GET.get("page", "1"))
    except (ValueError, TypeError):
        page = 1

    context = {
        "planb_user": request.planb_user,
    }

    try:
        context["groups_page"] = group_service.list_groups(
            session_service.get_access_token(request),
            page=page,
        )
    except (GroupUnavailable, InvalidGroup):
        context["groups_unavailable"] = True
        return render(
            request,
            "groups/index.html",
            context,
            status=503,
        )

    return render(request, "groups/index.html", context)

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
    people_context = {}
    try:
        people_context['members'] = invitation_service.list_members(access_token, group_id)
        if group['role'] == 'owner':
            people_context['pending_invitations'] = invitation_service.list_pending(access_token, group_id)
    except (InvitationUnavailable, InvitationNotFound):
        people_context['members_unavailable'] = True
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
            **people_context,
        }, status=503)
    plans_by_date = [
        {"date": date, "items": list(items)}
        for date, items in groupby(all_plans, key=lambda plan: plan["created_at"].date())
    ]
    return render(request, "groups/detail.html", {
        "group": group, "plans_page": {"items": all_plans},
        "plans_by_date": plans_by_date, "planb_user": request.planb_user,
        **people_context,
    })


@private_page
@require_http_methods(['GET', 'POST'])
@supabase_login_required
@sensitive_variables()
def invite(request, group_id):
    try:
        group = _owned_group(request, group_id)
    except GroupNotFound:
        raise Http404('No encontramos ese grupo.') from None
    except (GroupUnavailable, InvalidGroup):
        return _management_unavailable(request, group_id)
    token = session_service.get_access_token(request)
    search_form = InviteSearchForm(request.GET if 'username' in request.GET else None)
    invite_form = InviteForm(request.POST if request.method == 'POST' else None)
    candidates, pending, status = [], [], 200
    error_message = None
    try:
        if request.method == 'POST' and invite_form.is_valid():
            invitation_service.create_invitation(token, group_id, invite_form.cleaned_data['invited_user_id'])
            messages.success(request, 'La invitación se envió. La persona podrá responder desde sus notificaciones.')
            return redirect('groups:detail', group_id=group_id)
        pending = invitation_service.list_pending(token, group_id)
        if search_form.is_bound and search_form.is_valid():
            candidates = invitation_service.search_invitees(token, group_id, search_form.cleaned_data['username'])
            pending_users = {item['invited_user_id'] for item in pending}
            for candidate in candidates:
                candidate['pending'] = candidate['id'] in pending_users
    except InvitationNotFound:
        raise Http404('No encontramos ese grupo.') from None
    except (InvalidInvitation, InvitationConflict) as error:
        error_message = str(error)
        status = 409 if isinstance(error, InvitationConflict) else 400
    except InvitationUnavailable as error:
        error_message, status = str(error), 503
    return render(request, 'groups/invite.html', {
        'group': group, 'search_form': search_form, 'invite_form': invite_form,
        'candidates': candidates, 'pending_invitations': pending,
        'error_message': error_message, 'planb_user': request.planb_user,
    }, status=status)

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
