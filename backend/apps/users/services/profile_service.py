"""Acceso mínimo a perfiles; el JWT propio siempre queda sujeto a RLS."""

import re
from uuid import UUID

from django.views.decorators.debug import sensitive_variables

from . import clients
from .exceptions import ServiceUnavailable

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,29}\Z")


def _normalized_username(username):
    value = username.strip()
    return value.lower() if USERNAME_PATTERN.fullmatch(value) else None


@sensitive_variables()
def _find_user_id(client, normalized):
    rows = (
        client.table("profiles")
        .select("id")
        .eq("username_normalized", normalized)
        .limit(1)
        .execute()
        .data
    )
    if not isinstance(rows, list):
        raise ServiceUnavailable()
    return str(UUID(rows[0]["id"])) if rows else None


@sensitive_variables()
def username_available(username):
    normalized = _normalized_username(username)
    if normalized is None:
        return False
    try:
        with clients.administrative_client() as client:
            return _find_user_id(client, normalized) is None
    except Exception:
        # No propagar payloads PostgREST/Auth ni registrar datos del usuario.
        raise ServiceUnavailable() from None


@sensitive_variables()
def resolve_email(username):
    normalized = _normalized_username(username)
    if normalized is None:
        return None
    try:
        with clients.administrative_client() as client:
            user_id = _find_user_id(client, normalized)
            if user_id is None:
                return None
            result = client.auth.admin.get_user_by_id(user_id)
            user = result.user if result else None
            return user.email if user and user.email else None
    except Exception as error:
        if getattr(error, "code", None) == "user_not_found":
            return None
        raise ServiceUnavailable() from None


@sensitive_variables()
def get_profile(access_token, user_id):
    if not access_token:
        raise ServiceUnavailable()
    try:
        canonical_id = str(UUID(user_id))
        with clients.public_client(access_token=access_token) as client:
            rows = (
                client.table("profiles")
                .select("id,username")
                .eq("id", canonical_id)
                .limit(1)
                .execute()
                .data
            )
        if not isinstance(rows, list) or len(rows) != 1:
            raise ServiceUnavailable()
        row = rows[0]
        if str(UUID(row["id"])) != canonical_id or not isinstance(row["username"], str):
            raise ServiceUnavailable()
        return {"id": canonical_id, "username": row["username"]}
    except Exception:
        raise ServiceUnavailable() from None


@sensitive_variables()
def usernames_for_ids(user_ids):
    """Resolve only the profile IDs already visible through an authorized domain query."""
    try:
        canonical_ids = sorted({str(UUID(str(user_id))) for user_id in user_ids})
        if not canonical_ids:
            return {}
        with clients.administrative_client() as client:
            rows = (client.table("profiles").select("id,username")
                    .in_("id", canonical_ids).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        result = {}
        for row in rows:
            user_id = str(UUID(row["id"]))
            if user_id not in canonical_ids or not isinstance(row["username"], str):
                raise ValueError
            result[user_id] = row["username"]
        return result
    except Exception:
        raise ServiceUnavailable() from None
