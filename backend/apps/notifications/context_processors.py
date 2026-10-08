from django.views.decorators.debug import sensitive_variables

from apps.groups.services.invitation_service import InvitationUnavailable
from apps.users.services import session_service
from . import services


@sensitive_variables()
def notification_badge(request):
    if not getattr(request, 'planb_user', None):
        return {}
    # Una sola consulta por render; fallo del badge no bloquea grupos ni sesiones.
    if not hasattr(request, '_notification_badge'):
        try:
            request._notification_badge = {
                'notifications_unread': services.unread_count(session_service.get_access_token(request)),
            }
        except InvitationUnavailable:
            request._notification_badge = {'notifications_badge_unavailable': True}
    return request._notification_badge
