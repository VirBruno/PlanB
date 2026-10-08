"""Invitaciones remotas: publishable key + JWT, RPCs sin identidad del cliente."""
from uuid import UUID

from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired


class InvitationUnavailable(Exception):
    def __init__(self):
        super().__init__('No pudimos confirmar la operación. Revisá el estado antes de volver a intentar.')


class InvitationNotFound(Exception):
    pass


class InvalidInvitation(Exception):
    def __init__(self):
        super().__init__('Revisá el username: no podés invitarte ni invitar a alguien que ya integra el grupo.')


class InvitationConflict(Exception):
    def __init__(self):
        super().__init__('La invitación ya fue respondida, está pendiente o el usuario ya integra el grupo. Actualizá la página.')


def canonical_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise InvitationNotFound() from None


@sensitive_variables()
def rpc(access_token, operation, params, *, read=False):
    if not access_token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=access_token) as client:
            return client.rpc(operation, params, get=read).execute().data
    except Exception as error:
        if isinstance(error, APIError):
            if error.code in ('PGRST301', 'PGRST303'):
                raise SessionExpired() from None
            if error.code == 'PT404':
                raise InvitationNotFound() from None
            if error.code in ('PT409', '23505'):
                raise InvitationConflict() from None
            if error.code in ('22023', '23514'):
                raise InvalidInvitation() from None
        raise InvitationUnavailable() from None


def _people(rows, *, members=False, pending=False):
    try:
        if not isinstance(rows, list):
            raise ValueError
        result = []
        for row in rows:
            person = {'id': str(UUID(row['id'])), 'username': row['username']}
            if not isinstance(person['username'], str):
                raise ValueError
            person['initials'] = person['username'][:2].upper()
            if members:
                person['role'] = row['role']
                if person['role'] not in ('owner', 'member'):
                    raise ValueError
                person['role_label'] = 'Owner' if person['role'] == 'owner' else 'Miembro'
            if pending:
                person['invited_user_id'] = str(UUID(row['invited_user_id']))
                person['created_at'] = parse_datetime(row['created_at'])
                if person['created_at'] is None:
                    raise ValueError
            result.append(person)
        return result
    except Exception:
        raise InvitationUnavailable() from None


@sensitive_variables()
def search_invitees(access_token, group_id, username):
    rows = rpc(access_token, 'search_group_invitees', {
        'p_group_id': canonical_id(group_id), 'p_username': username,
    }, read=True)
    people = _people(rows)
    if len(people) > 1:
        raise InvitationUnavailable()
    return people


@sensitive_variables()
def list_members(access_token, group_id):
    return _people(rpc(access_token, 'list_group_members', {
        'p_group_id': canonical_id(group_id),
    }, read=True), members=True)


@sensitive_variables()
def list_pending(access_token, group_id):
    return _people(rpc(access_token, 'list_pending_group_invitations', {
        'p_group_id': canonical_id(group_id),
    }, read=True), pending=True)


def _result_id(result):
    try:
        return str(UUID(result))
    except (ValueError, TypeError, AttributeError):
        raise InvitationUnavailable() from None


@sensitive_variables()
def create_invitation(access_token, group_id, invited_user_id):
    return _result_id(rpc(access_token, 'create_group_invitation', {
        'p_group_id': canonical_id(group_id), 'p_invited_user_id': canonical_id(invited_user_id),
    }))


@sensitive_variables()
def accept_invitation(access_token, invitation_id):
    return _result_id(rpc(access_token, 'accept_group_invitation', {
        'p_invitation_id': canonical_id(invitation_id),
    }))


@sensitive_variables()
def reject_invitation(access_token, invitation_id):
    invitation_id = canonical_id(invitation_id)
    result = _result_id(rpc(access_token, 'reject_group_invitation', {'p_invitation_id': invitation_id}))
    if result != invitation_id:
        raise InvitationUnavailable()
    return result
