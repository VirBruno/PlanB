"""Calendario personal dentro de la app que contiene dashboard y perfil."""
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET

from apps.groups.services.exceptions import GroupUnavailable, InvalidGroup
from apps.plans.services.exceptions import PlanUnavailable
from .calendar_month import build_month, selected_month
from .decorators import private_page, supabase_login_required
from .services import calendar_service, session_service


@private_page
@require_GET
@supabase_login_required
@sensitive_variables()
def index(request):
    today = timezone.localdate()
    first = selected_month(request.GET, today=today)
    events = []
    unavailable = False
    try:
        events = calendar_service.list_events(session_service.get_access_token(request), first)
    except (PlanUnavailable, GroupUnavailable, InvalidGroup):
        unavailable = True
    return render(request, "users/calendar.html", {
        **build_month(first, events, today=today),
        "calendar_unavailable": unavailable, "planb_user": request.planb_user,
    }, status=503 if unavailable else 200)
