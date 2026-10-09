"""Propuestas remotas: Data API + JWT del usuario; RLS controla el acceso."""
import logging
from decimal import Decimal, InvalidOperation
from math import isfinite
import re
import struct
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .proposal_exceptions import (
    InvalidProposal, ProposalAlreadyExists, ProposalNotFound, ProposalUnavailable,
)

PROPOSAL_TYPES = ("juntada", "reunión", "salida")
COLUMNS = "id,created_at,tittle,description,date_pick,created_by,plan_id,type,posicion,budget_min,budget_max"
logger = logging.getLogger("planb")


def _budget(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int, float)):
        raise InvalidProposal()
    try:
        # El SDK puede decodificar numeric como int/float; str recupera su forma
        # decimal. Nunca convertir Decimal a float para enviar importes.
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise InvalidProposal()
        DecimalValidator(max_digits=12, decimal_places=2)(amount)
    except (InvalidOperation, ValidationError, ValueError):
        raise InvalidProposal() from None
    return amount


def _budget_range(budget_min, budget_max):
    minimum, maximum = _budget(budget_min), _budget(budget_max)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise InvalidProposal()
    return minimum, maximum


def _budget_payload(budget_min, budget_max):
    minimum, maximum = _budget_range(budget_min, budget_max)
    # PostgREST acepta strings decimales para numeric sin redondeo binario.
    return {"budget_min": format(minimum, "f") if minimum is not None else None,
            "budget_max": format(maximum, "f") if maximum is not None else None}


def _uuid(value, error_type):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise error_type() from None


def _position(value):
    if isinstance(value, str):
        match = re.fullmatch(
            r"(?:SRID=(\d+);)?POINT\(\s*([-+\d.eE]+)\s+([-+\d.eE]+)\s*\)",
            value.strip(), re.IGNORECASE,
        )
        if match:
            if match.group(1) not in (None, "4326"):
                raise InvalidProposal()
            value = {
                "type": "Point",
                "coordinates": [float(match.group(2)), float(match.group(3))],
            }
        else:
            try:
                raw = bytes.fromhex(value.strip())
                if len(raw) < 5 or raw[0] not in (0, 1):
                    raise ValueError
                byte_order = "<" if raw[0] == 1 else ">"
                type_code = struct.unpack_from(f"{byte_order}I", raw, 1)[0]
                has_z = bool(type_code & 0x80000000)
                has_m = bool(type_code & 0x40000000)
                has_srid = bool(type_code & 0x20000000)
                if type_code & 0x0FFFFFFF != 1 or has_z or has_m:
                    raise ValueError
                offset = 5
                if has_srid:
                    srid = struct.unpack_from(f"{byte_order}I", raw, offset)[0]
                    offset += 4
                    if srid != 4326:
                        raise ValueError
                if len(raw) != offset + 16:
                    raise ValueError
                longitude, latitude = struct.unpack_from(f"{byte_order}dd", raw, offset)
                value = {"type": "Point", "coordinates": [longitude, latitude]}
            except (ValueError, struct.error):
                raise InvalidProposal() from None
    if not isinstance(value, dict) or value.get("type") != "Point":
        raise InvalidProposal()
    coordinates = value.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise InvalidProposal()
    longitude, latitude = coordinates
    if (not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float))
            or isinstance(longitude, bool) or isinstance(latitude, bool)):
        raise InvalidProposal()
    longitude, latitude = float(longitude), float(latitude)
    if (not isfinite(longitude) or not isfinite(latitude)
            or not -180 <= longitude <= 180 or not -90 <= latitude <= 90):
        raise InvalidProposal()
    return {"type": "Point", "coordinates": [longitude, latitude]}


def _position_ewkt(value):
    longitude, latitude = _position(value)["coordinates"]
    return f"SRID=4326;POINT({longitude:.15g} {latitude:.15g})"


def _proposal(row, expected_plan_id=None):
    result = {key: row[key] for key in COLUMNS.split(",")}
    if isinstance(result["id"], bool) or not isinstance(result["id"], int) or result["id"] < 1:
        raise ValueError
    result["created_by"] = _uuid(result["created_by"], ValueError)
    result["plan_id"] = _uuid(result["plan_id"], ValueError)
    if expected_plan_id and result["plan_id"] != expected_plan_id:
        raise ValueError
    if (not isinstance(result["tittle"], str)
            or result["description"] is not None and not isinstance(result["description"], str)
            or result["type"] not in PROPOSAL_TYPES):
        raise ValueError
    result["created_at"] = parse_datetime(result["created_at"])
    result["date_pick"] = (
        parse_datetime(result["date_pick"]) if result["date_pick"] else None
    )
    if result["created_at"] is None or result["date_pick"] is None and row["date_pick"]:
        raise ValueError
    try:
        result["posicion"] = _position(result["posicion"])
    except InvalidProposal:
        position = row["posicion"]
        coordinates = position.get("coordinates") if isinstance(position, dict) else None
        logger.warning(
            "Proposal response has unsupported position structure "
            "(python_type=%s, geojson_type=%s, keys=%s, coordinates_type=%s, coordinate_count=%s).",
            type(position).__name__,
            position.get("type") if isinstance(position, dict) else None,
            sorted(position.keys()) if isinstance(position, dict) else None,
            type(coordinates).__name__ if coordinates is not None else None,
            len(coordinates) if isinstance(coordinates, (list, tuple)) else None,
        )
        raise ProposalUnavailable() from None
    result["longitude"], result["latitude"] = result["posicion"]["coordinates"]
    try:
        result["budget_min"], result["budget_max"] = _budget_range(result["budget_min"], result["budget_max"])
    except InvalidProposal:
        raise ValueError from None
    return result


def _translate(error):
    if isinstance(error, ProposalUnavailable):
        raise error
    if isinstance(error, InvalidProposal):
        raise error
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "PT404":
            raise ProposalNotFound() from None
        if error.code == "23505":
            raise ProposalAlreadyExists() from None
        if error.code in ("22023", "22P02", "23514"):
            raise InvalidProposal() from None
        if error.code == "42501":
            logger.warning(
                "Proposal Data API insufficient privilege (message=%s).",
                error.message,
            )
            raise ProposalUnavailable() from None
        logger.warning("Proposal Data API request failed (code=%s).", error.code)
    else:
        logger.warning(
            "Unexpected proposal Data API failure (type=%s).", type(error).__name__,
        )
    raise ProposalUnavailable() from None


def _date_value(date_pick):
    return date_pick.isoformat() if date_pick else None


@sensitive_variables()
def list_proposals(access_token, plan_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, InvalidProposal)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").select(COLUMNS)
                    .eq("plan_id", canonical_plan_id)
                    .order("created_at", desc=True).order("id", desc=True)
                    .execute().data)
        if not isinstance(rows, list):
            raise ValueError
        return [_proposal(row, canonical_plan_id) for row in rows]
    except Exception as error:
        _translate(error)


@sensitive_variables()
def has_user_proposal(access_token, *, plan_id, created_by):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, InvalidProposal)
    actor_id = _uuid(created_by, InvalidProposal)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").select("id")
                    .eq("plan_id", canonical_plan_id).eq("created_by", actor_id)
                    .limit(1).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        return bool(rows)
    except Exception as error:
        _translate(error)


@sensitive_variables()
def get_proposal(access_token, *, plan_id, proposal_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, ProposalNotFound)
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ProposalNotFound()
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").select(COLUMNS)
                    .eq("plan_id", canonical_plan_id).eq("id", proposal_id)
                    .limit(1).execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise ProposalNotFound()
        proposal = _proposal(rows[0], canonical_plan_id)
        if proposal["id"] != proposal_id:
            raise ValueError
        return proposal
    except ProposalNotFound:
        raise
    except Exception as error:
        _translate(error)


@sensitive_variables()
def create_proposal(access_token, *, plan_id, created_by, tittle, description,
                    date_pick, proposal_type, position, budget_min=None, budget_max=None):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, InvalidProposal)
    actor_id = _uuid(created_by, InvalidProposal)
    point = _position(position)
    if proposal_type not in PROPOSAL_TYPES:
        raise InvalidProposal()
    budget = _budget_payload(budget_min, budget_max)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").insert({
                "plan_id": canonical_plan_id, "created_by": actor_id,
                "tittle": tittle, "description": description or None,
                "date_pick": _date_value(date_pick), "type": proposal_type,
                "posicion": _position_ewkt(point),
                **budget,
            }).select("id").execute().data)
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError
        proposal_id = rows[0]["id"]
        if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
            raise ValueError
        return proposal_id
    except Exception as error:
        _translate(error)


@sensitive_variables()
def update_proposal(access_token, *, plan_id, proposal_id, tittle, description,
                    date_pick, proposal_type, position, budget_min=None, budget_max=None):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, ProposalNotFound)
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ProposalNotFound()
    point = _position(position)
    if proposal_type not in PROPOSAL_TYPES:
        raise InvalidProposal()
    budget = _budget_payload(budget_min, budget_max)
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").update({
                "tittle": tittle, "description": description or None,
                "date_pick": _date_value(date_pick), "type": proposal_type,
                "posicion": _position_ewkt(point),
                **budget,
            }).eq("plan_id", canonical_plan_id).eq("id", proposal_id)
                    .select("id").execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise ProposalNotFound()
        if rows[0]["id"] != proposal_id:
            raise ValueError
        return proposal_id
    except ProposalNotFound:
        raise
    except Exception as error:
        _translate(error)


@sensitive_variables()
def delete_proposal(access_token, *, plan_id, proposal_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = _uuid(plan_id, ProposalNotFound)
    if isinstance(proposal_id, bool) or not isinstance(proposal_id, int) or proposal_id < 1:
        raise ProposalNotFound()
    try:
        with clients.public_client(access_token=access_token) as client:
            rows = (client.table("proposals").delete()
                    .eq("plan_id", canonical_plan_id).eq("id", proposal_id)
                    .select("id").execute().data)
        if not isinstance(rows, list):
            raise ValueError
        if not rows:
            raise ProposalNotFound()
        if rows[0]["id"] != proposal_id:
            raise ValueError
        return proposal_id
    except ProposalNotFound:
        raise
    except Exception as error:
        _translate(error)
