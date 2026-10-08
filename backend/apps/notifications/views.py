from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from apps.groups.services import invitation_service
from apps.groups.services.invitation_service import (
    InvitationConflict, InvitationNotFound, InvitationUnavailable, InvalidInvitation,
)
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from . import services


def _unavailable(request):
    return render(request, 'notifications/index.html', {
        'unavailable': True, 'planb_user': request.planb_user,
    }, status=503)


@private_page
@require_GET
@supabase_login_required
@sensitive_variables()
def index(request):
    try:
        page = int(request.GET.get('page', '1'))
    except (ValueError, TypeError):
        page = 1
    try:
        notifications = services.list_notifications(session_service.get_access_token(request), page=page)
    except InvitationUnavailable:
        return _unavailable(request)
    return render(request, 'notifications/index.html', {
        'notifications_page': notifications, 'planb_user': request.planb_user,
    })


def _respond(request, invitation_id, operation):
    try:
        operation(session_service.get_access_token(request), invitation_id)
    except InvitationNotFound:
        raise Http404('No encontramos esa invitación.') from None
    except (InvitationConflict, InvalidInvitation) as error:
        messages.info(request, str(error))
    except InvitationUnavailable:
        return _unavailable(request)
    return redirect('notifications:index')


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def accept(request, invitation_id):
    return _respond(request, invitation_id, invitation_service.accept_invitation)


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def reject(request, invitation_id):
    return _respond(request, invitation_id, invitation_service.reject_invitation)


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def mark_read(request, notification_id):
    try:
        services.mark_read(session_service.get_access_token(request), notification_id)
    except InvitationNotFound:
        raise Http404('No encontramos esa notificación.') from None
    except InvitationUnavailable:
        return _unavailable(request)
    return redirect('notifications:index')
