"""Endpoints de backend para Prompt 1; no activan generación desde el detalle."""
from django.http import Http404, JsonResponse
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from .services import plan_recommendation_service
from .services.exceptions import PlanNotFound, PlanUnavailable
from .services.place_types import RecommendationError


@private_page
@require_http_methods(["GET", "POST"])
@supabase_login_required
@sensitive_variables()
def recommendations(request, plan_id):
    try:
        operation = (plan_recommendation_service.generate_recommendations if request.method == "POST"
                     else plan_recommendation_service.get_recommendations)
        result = operation(session_service.get_access_token(request), str(plan_id))
    except (PlanNotFound, GroupNotFound):
        raise Http404("No encontramos ese plan.") from None
    except (PlanUnavailable, GroupUnavailable, InvalidGroup, RecommendationError):
        return JsonResponse({"status": "unavailable", "error_code": "storage_unavailable",
                             "items": []}, status=503)
    return JsonResponse(result)
