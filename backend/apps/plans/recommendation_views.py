"""Consulta/generación y reintento explícito sobre el mismo servicio autorizado."""
from django.http import Http404, JsonResponse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from .services import plan_recommendation_service
from .services.exceptions import PlanNotFound, PlanUnavailable
from .services.place_types import RecommendationError


@sensitive_variables()
def _response(request, plan_id, *, retry=False):
    try:
        token = session_service.get_access_token(request)
        if retry:
            result = plan_recommendation_service.get_recommendations(token, str(plan_id))
            # No convertir Reintentar en un recálculo arbitrario de un ready.
            # La RPC existente mantiene el lock y la protección de pending reciente.
            if result["status"] in ("failed", "pending"):
                result = plan_recommendation_service.generate_recommendations(
                    token, str(plan_id), replace=True,
                )
            elif result["status"] == "not_generated":
                # Una solicitud anterior pudo fallar antes de reservar trabajo.
                result = plan_recommendation_service.generate_recommendations(token, str(plan_id))
        else:
            operation = (plan_recommendation_service.generate_recommendations if request.method == "POST"
                         else plan_recommendation_service.get_recommendations)
            result = operation(token, str(plan_id))
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (PlanUnavailable, GroupUnavailable, InvalidGroup, RecommendationError):
        return JsonResponse({"status": "unavailable", "error_code": "storage_unavailable",
                             "items": []}, status=503)
    return JsonResponse(result)


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
def recommendations(request, plan_id):
    return _response(request, plan_id)


@private_page
@require_POST
@supabase_login_required
def retry_recommendations(request, plan_id):
    return _response(request, plan_id, retry=True)
