"""Dominio remoto: Data API + JWT del usuario; RLS limita cada operación."""
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .exceptions import InvalidPlan, PlanNotFound, PlanUnavailable

PAGE_SIZE = 12
COLUMNS = "id,created_at,name,description,status,group_id,created_by"


def _canonical_uuid(value, error_type):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise error_type() from None


def _plan(row):
    result = {key: row[key] for key in COLUMNS.split(",")}
    result["id"] = _canonical_uuid(result["id"], ValueError)
    for key in ("group_id", "created_by"):
        result[key] = (
            _canonical_uuid(result[key], ValueError) if result[key] is not None else None
        )
    if (not isinstance(result["name"], str)
            or not isinstance(result["description"], str)
            or type(result["status"]) is not bool):
        raise ValueError
    result["created_at"] = parse_datetime(result["created_at"])
    if result["created_at"] is None:
        raise ValueError
    return result


def _translate(error):
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "PT404":
            raise PlanNotFound() from None
        if error.code in ("22023", "23514"):
            raise InvalidPlan() from None
    raise PlanUnavailable() from None


@sensitive_variables()
def list_plans(access_token, *, page=1, group_id=None):
    if not access_token:
        raise SessionExpired()
    if not isinstance(page, int) or not 1 <= page <= 10000:
        page = 1
    canonical_group = _canonical_uuid(group_id, InvalidPlan) if group_id else None
    start = (page - 1) * PAGE_SIZE
    try:
        with clients.public_client(access_token=access_token) as client:
            query = client.table("plans").select(COLUMNS)
            if canonical_group:
                query = query.eq("group_id", canonical_group)
            rows = (query.order("created_at", desc=True).order("id", desc=True)
                    .range(start, start + PAGE_SIZE).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        return {
            "items": [_plan(row) for row in rows[:PAGE_SIZE]],
            "has_next": len(rows) > PAGE_SIZE,
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(rows) > PAGE_SIZE else None,
        }
    except Exception as error:
        _translate(error)


@sensitive_variables()
def get_plan(access_token, plan_id):
    if not access_token:
        raise SessionExpired()
    canonical_id = _canonical_uuid(plan_id, PlanNotFound)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").select(COLUMNS)
                    .eq("id", canonical_id).limit(1).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise PlanNotFound()
        plan = _plan(rows[0])
        if plan["id"] != canonical_id:
            raise ValueError
        return plan
    except PlanNotFound:
        raise
    except Exception as error:
        _translate(error)


@sensitive_variables()
def create_plan(access_token, *, created_by, name, description, status, group_id):
    if not access_token:
        raise SessionExpired()
    actor_id = _canonical_uuid(created_by, InvalidPlan)
    canonical_group = _canonical_uuid(group_id, InvalidPlan)
    if type(status) is not bool:
        raise InvalidPlan()
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").insert({
                "name": name, "description": description, "status": status,
                "group_id": canonical_group, "created_by": actor_id,
            }).select("id").execute().data)
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        return _canonical_uuid(rows[0]["id"], ValueError)
    except Exception as error:
        _translate(error)


@sensitive_variables()
def update_plan(access_token, *, plan_id, name, description, status):
    if not access_token:
        raise SessionExpired()
    canonical_id = _canonical_uuid(plan_id, PlanNotFound)
    if type(status) is not bool:
        raise InvalidPlan()
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").update({
                "name": name, "description": description, "status": status,
            }).eq("id", canonical_id).select("id").execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise PlanNotFound()
        if _canonical_uuid(rows[0]["id"], ValueError) != canonical_id:
            raise ValueError
        return canonical_id
    except PlanNotFound:
        raise
    except Exception as error:
        _translate(error)


@sensitive_variables()
def delete_plan(access_token, plan_id):
    if not access_token:
        raise SessionExpired()
    canonical_id = _canonical_uuid(plan_id, PlanNotFound)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").delete()
                    .eq("id", canonical_id).select("id").execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise PlanNotFound()
        if _canonical_uuid(rows[0]["id"], ValueError) != canonical_id:
            raise ValueError
        return canonical_id
    except PlanNotFound:
        raise
    except Exception as error:
        _translate(error)