import json

from django.http import JsonResponse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST

from .decorators import private_page, supabase_login_required
from .services import session_service, visible_avatars


@private_page
@require_POST
@supabase_login_required
@sensitive_variables()
def avatars(request):
    try:
        if len(request.body) > 65536:
            raise ValueError
        payload = json.loads(request.body)
        if not isinstance(payload, dict) or set(payload) != {"avatars"}:
            raise ValueError
        items = visible_avatars.descriptors(payload["avatars"])
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"message": "Solicitud de avatares inválida."}, status=400)
    urls = visible_avatars.resolve(session_service.get_access_token(request), request.planb_user["id"], items)
    return JsonResponse({"urls": urls})
