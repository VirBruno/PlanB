"""API autenticada para la mesa de UNO entre integrantes."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from apps.groups.services import invitation_service
from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.groups.services.invitation_service import InvitationUnavailable
from apps.users.decorators import private_page, supabase_login_required
from apps.users.services import session_service
from .services import uno_duel
from .services.exceptions import PlanNotFound, PlanUnavailable
from .services.proposal_exceptions import InvalidProposal, ProposalUnavailable


def _disabled():
    return not (getattr(settings, 'UNO_MULTIPLAYER_ENABLED', False)
                and getattr(settings, 'UNO_SUPABASE_ENABLED', False))


def _error(error):
    if isinstance(error, PermissionDenied):
        return JsonResponse({'error': 'Esta partida no está disponible.'}, status=403)
    if isinstance(error, (PlanNotFound, GroupNotFound)):
        return JsonResponse({'error': 'No encontramos ese plan.'}, status=404)
    if isinstance(error, (InvalidProposal, ValueError, TypeError)):
        return JsonResponse({'error': 'La acción no está disponible. Actualizá la partida.'}, status=400)
    return JsonResponse({'error': 'No pudimos conectar. Probá de nuevo.'}, status=503)


@private_page
@require_http_methods(['GET', 'POST'])
@supabase_login_required
def lobby(request, plan_id):
    if _disabled():
        return JsonResponse({'error': 'El modo entre integrantes todavía no está disponible.'}, status=404)
    token = session_service.get_access_token(request)
    try:
        if request.method == 'GET':
            # Las operaciones se autorizan también dentro de las funciones de Supabase.
            from .views import _plan_and_group
            plan, _ = _plan_and_group(request, plan_id)
            members = invitation_service.list_members(token, plan['group_id'])
            return JsonResponse({'matches': uno_duel.list_matches(token, plan_id=plan_id),
                                 'members': [{'id': member['id'], 'username': member['username']}
                                             for member in members if member['id'] != str(request.planb_user['id'])]})
        action = request.POST.get('action')
        if action == 'invite':
            match_id = uno_duel.invite(token, plan_id=plan_id,
                proposal_id=int(request.POST.get('proposal_id', '')),
                invitee_id=request.POST.get('invitee_id'))
        elif action in ('accept', 'reject'):
            match_id = uno_duel.respond(token, match_id=request.POST.get('match_id'),
                accept=action == 'accept',
                proposal_id=int(request.POST.get('proposal_id', '')) if action == 'accept' else None)
        else:
            raise InvalidProposal()
        return JsonResponse({'match_id': match_id})
    except (InvalidProposal, ProposalUnavailable, PermissionDenied, InvitationUnavailable,
            PlanNotFound, PlanUnavailable, GroupNotFound, GroupUnavailable, InvalidGroup, ValueError, TypeError) as error:
        return _error(error)


@private_page
@require_http_methods(['GET', 'POST'])
@supabase_login_required
def game(request, match_id):
    if _disabled():
        return JsonResponse({'error': 'El modo entre integrantes todavía no está disponible.'}, status=404)
    token = session_service.get_access_token(request)
    try:
        source = request.GET if request.method == 'GET' else request.POST
        action = 'status' if request.method == 'GET' else source.get('action', '')
        state = uno_duel.game(token, match_id=match_id, action=action,
            card_id=source.get('card_id') or None, color=source.get('color') or None,
            expected_version=int(source.get('expected_version', '')) if action != 'status' else None,
            after_version=int(source.get('after_version', '0')))
        return JsonResponse({'game': state})
    except (InvalidProposal, ProposalUnavailable, PermissionDenied, ValueError, TypeError) as error:
        return _error(error)
