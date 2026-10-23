"""Confirma/reabre decisiones mediante RPC autenticada; nunca usa service_role."""
from uuid import UUID

from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from .exceptions import InvalidPlan, PlanUnavailable


def _id(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise InvalidPlan() from None


def _error(error):
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code in ("22023", "23514", "42501"):
            raise InvalidPlan() from None
    raise PlanUnavailable() from None


@sensitive_variables()
def confirm(token, *, plan_id, proposal_id):
    if not token:
        raise SessionExpired()
    if type(proposal_id) is not int or proposal_id < 1:
        raise InvalidPlan()
    try:
        with clients.public_client(access_token=token) as client:
            value = client.rpc("confirm_plan_proposal", {
                "p_plan_id": _id(plan_id), "p_proposal_id": proposal_id,
            }).execute().data
        if value != proposal_id:
            raise ValueError
        return value
    except Exception as exc:
        _error(exc)


@sensitive_variables()
def reopen(token, *, plan_id):
    if not token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=token) as client:
            value = client.rpc("reopen_plan_decision", {
                "p_plan_id": _id(plan_id),
            }).execute().data
        if value is not True:
            raise ValueError
        return True
    except Exception as exc:
        _error(exc)
