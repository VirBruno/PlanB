"""Operaciones de elección respaldadas por RPC y el JWT del integrante."""
from uuid import UUID

from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from . import plan_service
from .proposal_exceptions import (
    ElectionFlexibilityUnavailable, ElectionSchemaUnavailable, InvalidProposal,
    ProposalAlreadyExists, ProposalNotFound, ProposalUnavailable,
)

MAX_SCORE = 99999


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise InvalidProposal() from None


def _translate(error):
    if isinstance(error, APIError):
        if error.code in ("PGRST301", "PGRST303"):
            raise SessionExpired() from None
        if error.code == "PT404":
            raise ProposalNotFound() from None
        if error.code == "23505":
            raise ProposalAlreadyExists() from None
        if error.code == "P0001":
            raise ElectionFlexibilityUnavailable() from None
        if error.code in ("PGRST202", "42883"):
            if "remove_proposal_vote" in (error.message or "").lower():
                raise ElectionFlexibilityUnavailable() from None
            raise ElectionSchemaUnavailable() from None
        if error.code == "22023" and "election already has results" in (error.message or "").lower():
            raise ElectionFlexibilityUnavailable() from None
        if error.code in ("22023", "22P02", "23514"):
            raise InvalidProposal() from None
    raise ProposalUnavailable() from None


def _proposal_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidProposal()
    return value


def _rpc_integer(data, *, nullable=False):
    if data is None and nullable:
        return None
    if isinstance(data, bool) or not isinstance(data, int) or data < 0:
        raise ValueError
    return data


@sensitive_variables()
def set_method(access_token, *, plan_id, method):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    if method not in plan_service.ELECTION_METHODS:
        raise InvalidProposal()
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("set_proposal_election_method", {
                "p_plan_id": canonical_plan_id, "p_method": method,
            }).execute().data
        if data != method:
            raise ValueError
        return data
    except Exception as error:
        _translate(error)


@sensitive_variables()
def voted_proposal(access_token, *, plan_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("proposal_vote_for_user", {
                "p_plan_id": canonical_plan_id,
            }).execute().data
        if data == []:
            data = None
        return _proposal_id(data) if data is not None else None
    except Exception as error:
        _translate(error)


@sensitive_variables()
def cast_vote(access_token, *, plan_id, proposal_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    canonical_proposal_id = _proposal_id(proposal_id)
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("cast_proposal_vote", {
                "p_plan_id": canonical_plan_id,
                "p_proposal_id": canonical_proposal_id,
            }).execute().data
        return _rpc_integer(data)
    except Exception as error:
        _translate(error)


@sensitive_variables()
def remove_vote(access_token, *, plan_id):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("remove_proposal_vote", {
                "p_plan_id": canonical_plan_id,
            }).execute().data
        if data == []:
            data = None
        return _proposal_id(data) if data is not None else None
    except Exception as error:
        _translate(error)


@sensitive_variables()
def save_score(access_token, *, plan_id, proposal_id, score):
    if not access_token:
        raise SessionExpired()
    canonical_plan_id = plan_service._canonical_uuid(plan_id, InvalidProposal)
    canonical_proposal_id = _proposal_id(proposal_id)
    if (isinstance(score, bool) or not isinstance(score, int)
            or not 0 <= score <= MAX_SCORE):
        raise InvalidProposal()
    try:
        with clients.public_client(access_token=access_token) as client:
            data = client.rpc("save_proposal_score", {
                "p_plan_id": canonical_plan_id,
                "p_proposal_id": canonical_proposal_id,
                "p_score": score,
            }).execute().data
        return _rpc_integer(data)
    except Exception as error:
        _translate(error)