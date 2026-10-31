"""Encuentros personales: fecha definitiva y permisos existentes de grupos."""
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.groups.services import group_service
from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import plan_service
from apps.plans.services.exceptions import PlanUnavailable
from apps.users.calendar_month import month_bounds


@sensitive_variables()
def list_events(access_token, first):
    start, end = month_bounds(first)
    plans = plan_service.list_meetings(access_token, start=start, end=end)
    groups = {}
    events = []
    for plan in plans:
        # La misma autorización de grupo usada al entrar al detalle del plan.
        group_id = plan["group_id"]
        if not group_id or not plan.get("selected_proposal_id"):
            continue
        scheduled = plan.get("scheduled_at")
        if scheduled is None:
            continue
        if timezone.is_naive(scheduled):
            raise PlanUnavailable()
        if not start <= scheduled < end:
            continue
        if group_id not in groups:
            try:
                groups[group_id] = group_service.get_group(access_token, group_id)
            except GroupNotFound:
                # También excluye al creador si ya no pertenece al grupo.
                groups[group_id] = None
        if groups[group_id] is None:
            continue
        events.append({
            "id": plan["id"], "name": plan["name"],
            "group_name": groups[group_id]["name"],
            "group_id": group_id,  # Identidad visual del grupo; sin consultas ni permisos adicionales.
            "phase": "confirmed" if plan["status"] else "finished",
            "status_label": "Confirmado" if plan["status"] else "Finalizado",
            # confirm_plan_proposal copia date_pick a scheduled_at y lo congela.
            "starts_at": timezone.localtime(scheduled),
        })
    return sorted(events, key=lambda event: (event["starts_at"], event["id"]))
