"""Adaptador de partidas UNO entre integrantes; la autoridad está en Supabase."""

from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.views.decorators.debug import sensitive_variables
from postgrest.exceptions import APIError

from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired
from . import uno_game
from .proposal_exceptions import InvalidProposal, ProposalUnavailable
from .uno_supabase import _card


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise InvalidProposal() from None


def _integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise InvalidProposal()
    return value


@sensitive_variables()
def _rpc(access_token, name, params):
    if not access_token:
        raise SessionExpired()
    try:
        with clients.public_client(access_token=access_token) as client:
            return client.rpc(name, params).execute().data
    except APIError as error:
        if error.code in ('PGRST301', 'PGRST303'):
            raise SessionExpired() from None
        if error.code == '42501':
            raise PermissionDenied from None
        if error.code in ('22023', '22P02', '23505', '23514'):
            raise InvalidProposal() from None
        raise ProposalUnavailable() from None
    except Exception:
        raise ProposalUnavailable() from None


def _matches(data):
    if not isinstance(data, list) or len(data) > 50:
        raise ValueError
    result = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError
        item = {key: row.get(key) for key in (
            'status', 'role', 'opponent_username', 'proposal_id', 'opponent_proposal_id',
            'winner', 'finish_reason', 'version',
        )}
        item['id'] = str(UUID(str(row['id'])))
        item['opponent_id'] = str(UUID(str(row['opponent_id'])))
        if (item['status'] not in ('pending', 'playing', 'rejected', 'cancelled', 'finished')
                or item['role'] not in ('inviter', 'invitee')
                or not isinstance(item['opponent_username'], str) or not item['opponent_username']
                or item['winner'] not in (None, 'player', 'opponent')
                or item['finish_reason'] not in (None, 'played', 'forfeit')
                or type(item['version']) is not int or item['version'] < 0):
            raise ValueError
        for key in ('proposal_id', 'opponent_proposal_id'):
            if item[key] is not None and (type(item[key]) is not int or item[key] < 1):
                raise ValueError
        result.append(item)
    return result


def _game(data):
    if not isinstance(data, dict) or not isinstance(data.get('hand'), list):
        raise ValueError
    result = {key: data.get(key) for key in (
        'status', 'color', 'turn', 'winner', 'drawn', 'opponent_count', 'version',
        'proposal_id', 'opponent_proposal_id', 'finish_reason',
    )}
    result['id'] = str(UUID(str(data['id'])))
    result['opponent_id'] = str(UUID(str(data['opponent_id'])))
    if (result['status'] not in ('playing', 'finished') or result['color'] not in uno_game.COLORS
            or result['turn'] not in ('player', 'opponent', None)
            or result['winner'] not in ('player', 'opponent', None)
            or result['finish_reason'] not in ('played', 'forfeit', None)
            or type(result['drawn']) is not bool
            or type(result['version']) is not int or result['version'] < 0
            or type(result['opponent_count']) is not int or not 0 <= result['opponent_count'] <= 108
            or any(type(result[key]) is not int or result[key] < 1
                   for key in ('proposal_id', 'opponent_proposal_id'))
            or len(data['hand']) > 108):
        raise ValueError
    result['hand'] = [_card(card, playable=True) for card in data['hand']]
    result['top'] = _card(data.get('top'))
    if not isinstance(data.get('events'), list):
        raise ValueError
    result['events'] = []
    for event in data['events']:
        if (not isinstance(event, dict) or event.get('type') not in ('play', 'draw', 'uno', 'forfeit')
                or event.get('actor') not in ('player', 'opponent')
                or type(event.get('version')) is not int or not 1 <= event['version'] <= result['version']):
            raise ValueError
        clean = {key: event[key] for key in ('type', 'actor', 'version')}
        if event['type'] == 'play':
            value, color = event.get('value'), event.get('color')
            if (value not in (*map(str, range(10)), 'salto', 'reversa', 'mas_dos', *uno_game.WILDS)
                    or (value in uno_game.WILDS and color is not None)
                    or (value not in uno_game.WILDS and color not in uno_game.COLORS)):
                raise ValueError
            clean.update(value=value, color=color)
        elif event['type'] == 'draw':
            if (type(event.get('count')) is not int or not 0 <= event['count'] <= 4
                    or event.get('reason') not in ('deck', 'mas_dos', 'mas_cuatro')):
                raise ValueError
            clean.update(count=event['count'], reason=event['reason'])
        result['events'].append(clean)
    return result


def list_matches(access_token, *, plan_id):
    data = _rpc(access_token, 'uno_duel_list', {'p_plan_id': _uuid(plan_id)})
    try:
        return _matches(data)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProposalUnavailable() from None


def invite(access_token, *, plan_id, proposal_id, invitee_id):
    data = _rpc(access_token, 'uno_duel_invite', {
        'p_plan_id': _uuid(plan_id), 'p_proposal_id': _integer(proposal_id, minimum=1),
        'p_invitee_id': _uuid(invitee_id),
    })
    try:
        return str(UUID(str(data)))
    except (ValueError, TypeError, AttributeError):
        raise ProposalUnavailable() from None


def respond(access_token, *, match_id, accept, proposal_id=None):
    if type(accept) is not bool or (accept and proposal_id is None) or (not accept and proposal_id is not None):
        raise InvalidProposal()
    match_id = _uuid(match_id)
    data = _rpc(access_token, 'uno_duel_respond', {
        'p_match_id': match_id, 'p_accept': accept,
        'p_proposal_id': _integer(proposal_id, minimum=1) if accept else None,
    })
    if data != match_id:
        raise ProposalUnavailable()
    return match_id


def game(access_token, *, match_id, action='status', card_id=None, color=None,
         expected_version=None, after_version=0):
    if action not in ('status', 'play', 'draw', 'pass', 'forfeit'):
        raise InvalidProposal()
    if color is not None and color not in uno_game.COLORS:
        raise InvalidProposal()
    if action != 'status':
        _integer(expected_version)
    data = _rpc(access_token, 'uno_duel_game', {
        'p_match_id': _uuid(match_id), 'p_action': action,
        'p_card_id': _uuid(card_id) if card_id is not None else None,
        'p_color': color, 'p_expected_version': expected_version,
        'p_after_version': _integer(after_version),
    })
    try:
        return _game(data)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProposalUnavailable() from None
