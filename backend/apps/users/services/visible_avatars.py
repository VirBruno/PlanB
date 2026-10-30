"""Fotos privadas por lotes: autorizar con JWT antes de usar Storage administrativo."""
from collections import defaultdict
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.groups.services import invitation_service
from apps.groups.services.invitation_service import InvitationNotFound, InvitationUnavailable, InvalidInvitation
from apps.plans.services import expense_service, plan_service, proposal_service
from apps.plans.services.exceptions import PlanNotFound, PlanUnavailable
from apps.plans.services.proposal_exceptions import ProposalUnavailable
from apps.users.services.exceptions import SessionExpired
from . import clients
from .avatar_service import BUCKET

MAX_AVATARS = 100


def _uuid(value):
    return str(UUID(str(value)))


def descriptors(value):
    if not isinstance(value, list) or len(value) > MAX_AVATARS:
        raise ValueError
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"scope", "context", "user", "username"}:
            raise ValueError
        scope = item.get("scope")
        if scope not in ("self", "group", "plan", "notification", "search"):
            raise ValueError
        context = _uuid(item.get("context")) if scope != "self" else ""
        user = _uuid(item.get("user")) if scope not in ("self", "notification") else ""
        username = item.get("username", "") if scope == "search" else ""
        if not isinstance(username, str) or len(username) > 30:
            raise ValueError
        result.append({"scope": scope, "context": context, "user": user, "username": username})
    return result


@sensitive_variables()
def _sign_authorized(user_ids):
    """Sólo recibe identidades provenientes de consultas de dominio autorizadas."""
    paths = {f"{uid}/avatar": uid for uid in user_ids}
    if not paths:
        return {}
    try:
        with clients.administrative_client() as client:
            rows = client.storage.from_(BUCKET).create_signed_urls(sorted(paths), 300)
        result = {}
        prefix = settings.SUPABASE_URL.rstrip("/") + f"/storage/v1/object/sign/{BUCKET}/"
        for row in rows:
            path, url = row.get("path"), row.get("signedURL")
            if path in paths and not row.get("error") and isinstance(url, str) and url.startswith(prefix):
                # Además de pertenecer al proveedor, debe ser exactamente el objeto autorizado.
                if urlsplit(url).path == urlsplit(prefix + path).path:
                    result[paths[path]] = url
        return result
    except Exception:
        return {}  # Storage caído o sin foto: conservar el avatar de respaldo.


@sensitive_variables()
def _notice_people(token, notice_ids):
    if not notice_ids:
        return {}
    try:
        with clients.public_client(access_token=token) as client:
            rows = client.table("notifications").select("id,creditor_id,invitation_id").in_("id", sorted(notice_ids)).execute().data
            if not isinstance(rows, list):
                return {}
            invitation_ids = {r["invitation_id"] for r in rows if r.get("invitation_id")}
            invitations = []
            if invitation_ids:
                invitations = client.table("group_invitations").select("id,invited_by").in_("id", sorted(invitation_ids)).execute().data
        inviters = {r["id"]: _uuid(r["invited_by"]) for r in invitations if r["id"] in invitation_ids}
        result = {}
        for row in rows:
            if row["id"] in notice_ids:
                uid = row.get("creditor_id") or inviters.get(row.get("invitation_id"))
                if uid:
                    result[row["id"]] = _uuid(uid)
        return result
    except APIError as error:
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        return {}
    except Exception:
        return {}


@sensitive_variables()
def resolve(token, actor_id, items):
    if not token:
        raise SessionExpired()
    actor_id = _uuid(actor_id)
    notice_people = _notice_people(token, {item["context"] for item in items if item["scope"] == "notification"})
    resolved = {}
    scopes = defaultdict(list)
    for index, item in enumerate(items):
        if item["scope"] == "self":
            resolved[index] = actor_id
        else:
            scopes[(item["scope"], item["context"])].append((index, item))
    for (scope, context), requested in scopes.items():
        try:
            if scope == "notification":
                # Una consulta por lote, privada por RLS; nunca IDs del payload.
                if context in notice_people:
                    for index, _ in requested:
                        resolved[index] = notice_people[context]
                continue
            if scope == "search":
                for index, item in requested:
                    people = invitation_service.search_invitees(token, context, item["username"])
                    if item["user"] in {p["id"] for p in people}:
                        resolved[index] = item["user"]
                continue
            group_id = context
            if scope == "plan":
                group_id = plan_service.get_plan(token, context)["group_id"]
            members = invitation_service.list_members(token, group_id)
            allowed = {p["id"] for p in members}
            requested_ids = {item["user"] for _, item in requested}
            if not requested_ids <= allowed:
                if scope == "group" and any(p["id"] == actor_id and p["role"] == "owner" for p in members):
                    allowed.update(p["invited_user_id"] for p in invitation_service.list_pending(token, group_id))
                elif scope == "plan":
                    # Autores y participantes históricos siguen visibles sólo a quien accede al plan.
                    allowed.update(p["created_by"] for p in proposal_service.list_proposals(token, context))
                    if not requested_ids <= allowed:
                        summary = expense_service.get_summary(token, plan_id=context, actor_id=actor_id)
                        allowed.update(p["id"] for p in summary["people"])
            for index, item in requested:
                if item["user"] in allowed:
                    resolved[index] = item["user"]
        except SessionExpired:
            raise
        except (InvitationNotFound, InvitationUnavailable, InvalidInvitation, PlanNotFound, PlanUnavailable,
                ProposalUnavailable, expense_service.ExpenseUnavailable, expense_service.InvalidExpense,
                ValueError, TypeError, KeyError):
            continue
    urls = _sign_authorized(set(resolved.values()))
    return [urls.get(resolved.get(index)) for index in range(len(items))]
