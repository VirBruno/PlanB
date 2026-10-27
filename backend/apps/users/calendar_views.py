"""Calendario personal dentro de la app que contiene dashboard y perfil."""
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET

from apps.groups.services.exceptions import GroupUnavailable, InvalidGroup
from apps.plans.services.exceptions import PlanUnavailable
from .calendar_month import build_month, selected_month
from .decorators import private_page, supabase_login_required
from .services import calendar_service, session_service


def _month_context(request, *, selected=None):
    today = timezone.localdate()
    first = selected_month(request.GET, today=today)
    events = []
    unavailable = False
    try:
        events = calendar_service.list_events(session_service.get_access_token(request), first)
    except (PlanUnavailable, GroupUnavailable, InvalidGroup):
        unavailable = True
    return {
        **build_month(first, events, today=today, selected=selected),
        "calendar_unavailable": unavailable, "planb_user": request.planb_user,
    }


@private_page
@require_GET
@supabase_login_required
@sensitive_variables()
def index(request):
    context = _month_context(request)
    return render(request, 'users/calendar.html', context,
                  status=503 if context['calendar_unavailable'] else 200)


@private_page
@require_GET
@supabase_login_required
@sensitive_variables()
def selector(request):
    try:
        selected = parse_date(request.GET.get('selected', ''))
    except (ValueError, TypeError):
        selected = None
    context = _month_context(request, selected=selected)
    # Fragmento privado sin shell/context processors: no consulta el badge por mes.
    return HttpResponse(render_to_string('users/calendar_selector.html', context),
                        status=503 if context['calendar_unavailable'] else 200)
