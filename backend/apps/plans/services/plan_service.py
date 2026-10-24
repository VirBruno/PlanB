"""Dominio remoto: Data API + JWT del usuario; RLS limita cada operación."""
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .exceptions import InvalidPlan, PlanNotFound, PlanUnavailable, PlanningSchemaUnavailable

PAGE_SIZE = 12
COLUMNS = "id,created_at,name,description,status,group_id,created_by,election_method"
LEGACY_COLUMNS = COLUMNS.removesuffix(",election_method")
PLANNING_FIELDS = "scheduled_at,weather_province,weather_city_id,weather_city_name"
PLANNING_COLUMNS = COLUMNS + "," + PLANNING_FIELDS
DECISION_COLUMNS = PLANNING_COLUMNS + ",selected_proposal_id,confirmed_at"  # ETAPA8_DECISION_PLAN
ELECTION_METHODS = ("ideal", "votes", "combat")


def _canonical_uuid(value, error_type):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise error_type() from None


def _plan(row):
    result = {
        key: row[key] for key in COLUMNS.split(",")
        if key != "election_method"
    }
    result["election_method"] = row.get("election_method")
    selected = row.get("selected_proposal_id")
    if selected is not None and (type(selected) is not int or selected < 1):
        raise ValueError
    result["selected_proposal_id"] = selected
    result["decision_schema"] = "selected_proposal_id" in row
    result["confirmed_at"] = parse_datetime(row["confirmed_at"]) if row.get("confirmed_at") else None
    if row.get("confirmed_at") and result["confirmed_at"] is None:
        raise ValueError
    result["planning_schema"] = "scheduled_at" in row
    result["scheduled_at"] = parse_datetime(row["scheduled_at"]) if row.get("scheduled_at") else None
    if row.get("scheduled_at") and result["scheduled_at"] is None:
        raise ValueError
    result["weather_province"] = row.get("weather_province")
    result["weather_city_id"] = row.get("weather_city_id")
    result["weather_city_name"] = row.get("weather_city_name")
    if result["scheduled_at"] is not None and selected is None:
        from .plan_weather_service import PROVINCES
        if (result["weather_province"] not in PROVINCES
                or not isinstance(result["weather_city_id"], int)
                or not isinstance(result["weather_city_name"], str)):
            raise ValueError
    result["id"] = _canonical_uuid(result["id"], ValueError)
    for key in ("group_id", "created_by"):
        result[key] = (
            _canonical_uuid(result[key], ValueError) if result[key] is not None else None
        )
    if (not isinstance(result["name"], str)
            or not isinstance(result["description"], str)
            or type(result["status"]) is not bool):
        raise ValueError
    if result["election_method"] not in (None, *ELECTION_METHODS):
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


def _missing_decision_columns(error):
    return (
        isinstance(error, APIError)
        and error.code in ("42703", "PGRST204")
        and any(field in (error.message or "").lower()
                for field in ("selected_proposal_id", "confirmed_at"))
    )


def _missing_planning_columns(error):
    return (
        isinstance(error, APIError)
        and error.code in ("42703", "PGRST204")
        and any(field in (error.message or "").lower() for field in PLANNING_FIELDS.split(","))
    )


def _planning_payload(scheduled_at, weather_province, weather_city_id, weather_city_name):
    if scheduled_at is None:
        if any(value is not None for value in (weather_province, weather_city_id, weather_city_name)):
            raise InvalidPlan()
        return dict.fromkeys(PLANNING_FIELDS.split(","), None)
    from datetime import datetime
    from django.utils import timezone
    from .plan_weather_service import PROVINCES
    if (not isinstance(scheduled_at, datetime) or not timezone.is_aware(scheduled_at)
            or weather_province not in PROVINCES or type(weather_city_id) is not int
            or not 0 < weather_city_id < 2**31 or not isinstance(weather_city_name, str)
            or not 1 <= len(weather_city_name) <= 120):
        raise InvalidPlan()
    return {"scheduled_at": scheduled_at.isoformat(), "weather_province": weather_province,
            "weather_city_id": weather_city_id, "weather_city_name": weather_city_name}


def _missing_election_method(error):
    return (
        isinstance(error, APIError)
        and error.code in ("42703", "PGRST204")
        and "election_method" in (error.message or "").lower()
    )


def _list_plan_rows(client, *, columns, group_id, start):
    query = client.table("plans").select(columns)
    if group_id:
        query = query.eq("group_id", group_id)
    return (query.order("created_at", desc=True).order("id", desc=True)
            .range(start, start + PAGE_SIZE).execute().data)


def _get_plan_rows(client, *, columns, plan_id):
    return (client.table("plans").select(columns)
            .eq("id", plan_id).limit(1).execute().data)


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
            try:
                rows = _list_plan_rows(
                    client, columns=DECISION_COLUMNS, group_id=canonical_group, start=start,
                )
            except APIError as error:
                if _missing_decision_columns(error):
                    rows = _list_plan_rows(client, columns=PLANNING_COLUMNS,
                                           group_id=canonical_group, start=start)
                elif _missing_election_method(error):
                    rows = _list_plan_rows(client, columns=LEGACY_COLUMNS, group_id=canonical_group, start=start)
                elif _missing_planning_columns(error):
                    try:
                        rows = _list_plan_rows(client, columns=COLUMNS, group_id=canonical_group, start=start)
                    except APIError as fallback:
                        if not _missing_election_method(fallback):
                            raise
                        rows = _list_plan_rows(client, columns=LEGACY_COLUMNS, group_id=canonical_group, start=start)
                else:
                    raise
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
            try:
                rows = _get_plan_rows(client, columns=DECISION_COLUMNS, plan_id=canonical_id)
            except APIError as error:
                if _missing_decision_columns(error):
                    rows = _get_plan_rows(client, columns=PLANNING_COLUMNS, plan_id=canonical_id)
                elif _missing_election_method(error):
                    rows = _get_plan_rows(client, columns=LEGACY_COLUMNS, plan_id=canonical_id)
                elif _missing_planning_columns(error):
                    try:
                        rows = _get_plan_rows(client, columns=COLUMNS, plan_id=canonical_id)
                    except APIError as fallback:
                        if not _missing_election_method(fallback):
                            raise
                        rows = _get_plan_rows(client, columns=LEGACY_COLUMNS, plan_id=canonical_id)
                else:
                    raise
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
def create_plan(access_token, *, created_by, name, description, status, group_id,
                scheduled_at=None, weather_province=None, weather_city_id=None, weather_city_name=None):
    if not access_token:
        raise SessionExpired()
    actor_id = _canonical_uuid(created_by, InvalidPlan)
    canonical_group = _canonical_uuid(group_id, InvalidPlan)
    if type(status) is not bool:
        raise InvalidPlan()
    planner = _planning_payload(scheduled_at, weather_province, weather_city_id, weather_city_name)
    data = {"name": name, "description": description, "status": status,
            "group_id": canonical_group, "created_by": actor_id}
    if scheduled_at is not None:
        data.update(planner)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").insert(data).select("id").execute().data)
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        return _canonical_uuid(rows[0]["id"], ValueError)
    except Exception as error:
        if _missing_planning_columns(error):
            raise PlanningSchemaUnavailable() from None
        _translate(error)


@sensitive_variables()
def update_plan(access_token, *, plan_id, name, description, status,
                scheduled_at=None, weather_province=None, weather_city_id=None, weather_city_name=None,
                update_planning=False):
    if not access_token:
        raise SessionExpired()
    canonical_id = _canonical_uuid(plan_id, PlanNotFound)
    if type(status) is not bool:
        raise InvalidPlan()
    data = {"name": name, "description": description, "status": status}
    if update_planning:
        data.update(_planning_payload(scheduled_at, weather_province, weather_city_id, weather_city_name))
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("plans").update(data).eq("id", canonical_id).select("id").execute().data)
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
        if _missing_planning_columns(error):
            raise PlanningSchemaUnavailable() from None
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