"""Dominio remoto: Data API + JWT del usuario, nunca ORM ni secret key."""
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .exceptions import GroupNotFound, GroupUnavailable, InvalidGroup

PAGE_SIZE = 12
COLUMNS = "id,name,description,created_by,created_at,updated_at"


def _group(row):
    # Lista explícita: no entregar respuestas arbitrarias del proveedor al template.
    result = {key: row[key] for key in COLUMNS.split(",")}
    result["id"] = str(UUID(result["id"]))
    result["created_by"] = str(UUID(result["created_by"]))
    if not isinstance(result["name"], str) or not (
        result["description"] is None or isinstance(result["description"], str)
    ):
        raise ValueError
    for key in ("created_at", "updated_at"):
        result[key] = parse_datetime(result[key])
        if result[key] is None:
            raise ValueError
    return result


def _translate(error):
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code in ("22023", "23514"):
            raise InvalidGroup() from None
    raise GroupUnavailable() from None


@sensitive_variables()
def create_group(access_token, *, name, description=""):
    if not access_token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=access_token) as client:
            result = client.rpc("create_group", {
                "p_name": name, "p_description": description or None,
            }).execute().data
        return str(UUID(result))
    except Exception as error:
        _translate(error)


@sensitive_variables()
def list_groups(access_token, *, page=1):
    if not access_token:
        raise SessionExpired()
    if not isinstance(page, int) or not 1 <= page <= 10000:
        page = 1
    start = (page - 1) * PAGE_SIZE
    try:
        with clients.public_client(access_token=access_token) as client:
            # RLS filtra por pertenencia, incluso cuando el usuario no es creador.
            rows = (client.table("groups").select(COLUMNS)
                    .order("created_at", desc=True).order("id", desc=True)
                    .range(start, start + PAGE_SIZE).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        return {
            "items": [_group(row) for row in rows[:PAGE_SIZE]],
            "has_next": len(rows) > PAGE_SIZE,
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(rows) > PAGE_SIZE else None,
        }
    except Exception as error:
        _translate(error)


@sensitive_variables()
def get_group(access_token, group_id):
    if not access_token:
        raise SessionExpired()
    try:
        group_id = str(UUID(str(group_id)))
    except (ValueError, TypeError, AttributeError):
        raise GroupNotFound() from None
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("groups").select(COLUMNS)
                    .eq("id", group_id).limit(1).execute().data)
            if not isinstance(rows, list):
                raise ValueError
            if not rows:
                raise GroupNotFound()
            group = _group(rows[0])
            if group["id"] != group_id:
                raise ValueError
            memberships = (client.table("group_members").select("role")
                           .eq("group_id", group_id).limit(1).execute().data)
            # RLS permite únicamente la membresía propia, sin exponer otros usuarios.
            if not isinstance(memberships, list) or len(memberships) != 1:
                raise GroupNotFound()
            role = memberships[0]["role"]
            if role not in ("owner", "member"):
                raise ValueError
            group["role_label"] = "Owner" if role == "owner" else "Miembro"
        return group
    except GroupNotFound:
        raise
    except Exception as error:
        _translate(error)
