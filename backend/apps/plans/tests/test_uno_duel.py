from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.plans.services import uno_duel
from apps.plans.services.proposal_exceptions import InvalidProposal, ProposalUnavailable
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row, proposal_row
from apps.notifications import services as notification_services


MATCH = str(uuid4())
RIVAL = str(uuid4())
CARD = str(uuid4())
TOP = str(uuid4())


def state(**changes):
    result = {
        'id': MATCH, 'status': 'playing', 'color': 'rojo', 'turn': 'player',
        'winner': None, 'drawn': False, 'opponent_count': 7, 'version': 0,
        'proposal_id': 47, 'opponent_proposal_id': 48, 'opponent_id': RIVAL,
        'finish_reason': None,
        'hand': [{'id': CARD, 'color': 'rojo', 'value': '3', 'playable': True}],
        'top': {'id': TOP, 'color': 'rojo', 'value': '2'}, 'events': [],
    }
    result.update(changes)
    return result


class UnoDuelAdapterTests(SimpleTestCase):
    @patch('apps.plans.services.uno_duel._rpc')
    def test_game_uses_jwt_and_removes_private_and_unexpected_fields(self, rpc):
        rpc.return_value = state(deck=['secret'], opponent_hand=['secret'])
        result = uno_duel.game('user-jwt', match_id=MATCH, action='play', card_id=CARD,
                               expected_version=0, after_version=0)
        self.assertNotIn('deck', result)
        self.assertNotIn('opponent_hand', result)
        self.assertEqual(rpc.call_args.args[0], 'user-jwt')
        self.assertEqual(rpc.call_args.args[1], 'uno_duel_game')
        self.assertEqual(rpc.call_args.args[2]['p_expected_version'], 0)

    @patch('apps.plans.services.uno_duel._rpc')
    def test_public_events_are_filtered_and_validated(self, rpc):
        rpc.return_value = state(version=1, events=[{'type': 'play', 'actor': 'opponent',
            'value': '3', 'color': 'rojo', 'version': 1, 'secret': 'hidden'}])
        result = uno_duel.game('user-jwt', match_id=MATCH)
        self.assertNotIn('secret', result['events'][0])
        rpc.return_value = state(version=1, events=[{'type': 'play', 'actor': 'opponent',
            'value': '3', 'color': 'rojo', 'version': 2}])
        with self.assertRaises(ProposalUnavailable):
            uno_duel.game('user-jwt', match_id=MATCH)

    @patch('apps.plans.services.uno_duel._rpc')
    def test_invalid_requests_are_not_sent(self, rpc):
        for kwargs in ({'action': 'play', 'card_id': CARD},
                       {'action': 'play', 'card_id': 'invalid', 'expected_version': 0},
                       {'action': 'draw', 'expected_version': -1}):
            with self.assertRaises(InvalidProposal):
                uno_duel.game('user-jwt', match_id=MATCH, **kwargs)
        rpc.assert_not_called()

    @patch('apps.plans.services.uno_duel._rpc')
    def test_list_rejects_malformed_rows(self, rpc):
        rpc.return_value = [{'id': MATCH, 'opponent_id': RIVAL, 'status': 'playing',
            'role': 'invitee', 'opponent_username': 'Ana', 'proposal_id': 47,
            'opponent_proposal_id': 48, 'winner': None, 'finish_reason': None, 'version': 0,
            'game': {'deck': ['hidden']}}]
        self.assertNotIn('game', uno_duel.list_matches('user-jwt', plan_id=PLAN_ID)[0])
        rpc.return_value[0]['version'] = -1
        with self.assertRaises(ProposalUnavailable):
            uno_duel.list_matches('user-jwt', plan_id=PLAN_ID)

    def test_invited_member_without_a_proposal_sees_the_duel_lobby(self):
        proposal = proposal_row(created_by=RIVAL, tittle='Salir', Score=0, votes=0,
                                creator_username='Lu', can_confirm=False)
        html = render_to_string('plans/detail.html', {
            'plan': plan_row(election_method='combat', status=True, selected_proposal_id=None),
            'group': {'id': GROUP_ID, 'name': 'Amigos', 'role': 'member'},
            'planb_user': {'id': USER_ID, 'username': 'Ana'}, 'election_method': 'combat',
            'combat_game': 'uno', 'proposals': [proposal], 'my_proposal': None,
            'uno_multiplayer_enabled': True,
        }, request=RequestFactory().get('/'))
        self.assertIn('data-uno-duel', html)
        self.assertIn('proposal-uno-duel.js', html)
        self.assertIn('proposal-uno-mode.js', html)
        self.assertIn('data-uno-mode-choice="bot" aria-controls="uno-bot-panel" aria-expanded="false" disabled', html)
        self.assertIn('data-uno-mode-choice="grupo" aria-controls="uno-grupo-panel" aria-expanded="false"', html)
        self.assertIn('data-uno-mode-panel="grupo" hidden', html)
        self.assertNotIn('Creá tu propuesta para poder jugar por ella', html)

    def test_mode_selector_hides_both_mesas_until_a_choice(self):
        proposal = proposal_row(created_by=USER_ID, tittle='Salir', Score=0, votes=0,
                                creator_username='Ana', can_confirm=False)
        html = render_to_string('plans/detail.html', {
            'plan': plan_row(election_method='combat', status=True, selected_proposal_id=None),
            'group': {'id': GROUP_ID, 'name': 'Amigos', 'role': 'member'},
            'planb_user': {'id': USER_ID, 'username': 'Ana'}, 'election_method': 'combat',
            'combat_game': 'uno', 'proposals': [proposal], 'my_proposal': proposal,
            'uno_multiplayer_enabled': True,
        }, request=RequestFactory().get('/'))
        self.assertIn('data-uno-mode-panel="bot" hidden', html)
        self.assertIn('data-uno-mode-panel="grupo" hidden', html)
        self.assertNotIn('data-uno-mode-choice="bot" aria-controls="uno-bot-panel" aria-expanded="false" disabled', html)

    @patch('apps.notifications.services.rpc')
    def test_game_notification_uses_only_accessible_plan_reference(self, rpc):
        rpc.return_value = [{'id': MATCH, 'type': 'uno_duel_invitation',
            'created_at': '2026-11-12T12:00:00Z', 'read_at': None,
            'plan_id': PLAN_ID, 'plan_name': 'Salida', 'plan_available': True,
            'group_id': GROUP_ID, 'group_name': 'Amigos', 'secret': 'hidden'}]
        item = notification_services.list_notifications('user-jwt')['items'][0]
        self.assertEqual(item['type'], 'uno_duel_invitation')
        self.assertEqual(item['plan_id'], PLAN_ID)
        self.assertNotIn('secret', item)
        html = render_to_string('notifications/index.html', {
            'planb_user': {'id': USER_ID, 'username': 'Ana'},
            'notifications_page': {'items': [item], 'page': 1},
        }, request=RequestFactory().get('/'))
        self.assertIn('Te invitaron a jugar UNO', html)
        self.assertIn('?uno_mode=grupo#proposal-combat', html)


@override_settings(UNO_MULTIPLAYER_ENABLED=True, UNO_SUPABASE_ENABLED=True)
class UnoDuelViewsTests(TestCase):
    def setUp(self):
        patches = [
            patch('apps.users.services.session_service.current_user', return_value={'id': USER_ID, 'username': 'Ana'}),
            patch('apps.users.services.session_service.get_access_token', return_value='user-jwt'),
            patch('apps.plans.views._plan_and_group', return_value=(
                plan_row(election_method='combat', combat_game='uno', status=True, selected_proposal_id=None),
                {'id': GROUP_ID},
            )),
            patch('apps.plans.uno_duel_views.invitation_service.list_members', return_value=[
                {'id': USER_ID, 'username': 'Ana'}, {'id': RIVAL, 'username': 'Lu'},
            ]),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.lobby = f'/planes/{PLAN_ID}/uno/duelos/'
        self.game = f'/planes/uno/duelos/{MATCH}/'

    @patch('apps.plans.uno_duel_views.uno_duel.list_matches', return_value=[])
    def test_lobby_shows_other_members_and_matches(self, matches):
        response = self.client.get(self.lobby)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['members'], [{'id': RIVAL, 'username': 'Lu'}])
        matches.assert_called_once()

    @patch('apps.plans.uno_duel_views.uno_duel.invite', return_value=MATCH)
    def test_invite_uses_only_authenticated_user_and_validated_fields(self, invite):
        response = self.client.post(self.lobby, {'action': 'invite', 'proposal_id': '47',
            'invitee_id': RIVAL, 'inviter_id': RIVAL})
        self.assertEqual(response.json(), {'match_id': MATCH})
        self.assertEqual(invite.call_args.args, ('user-jwt',))
        self.assertEqual(invite.call_args.kwargs['proposal_id'], 47)
        self.assertNotIn('inviter_id', invite.call_args.kwargs)

    @patch('apps.plans.uno_duel_views.uno_duel.respond', return_value=MATCH)
    def test_accept_and_reject(self, respond):
        self.assertEqual(self.client.post(self.lobby, {'action': 'accept', 'match_id': MATCH,
            'proposal_id': '48'}).status_code, 200)
        self.assertEqual(respond.call_args.kwargs['proposal_id'], 48)
        self.assertEqual(self.client.post(self.lobby, {'action': 'reject', 'match_id': MATCH}).status_code, 200)
        self.assertIsNone(respond.call_args.kwargs['proposal_id'])

    @patch('apps.plans.uno_duel_views.uno_duel.game', return_value=state())
    def test_move_requires_version_and_has_no_client_identity(self, remote):
        self.assertEqual(self.client.post(self.game, {'action': 'play', 'card_id': CARD}).status_code, 400)
        self.assertEqual(self.client.post(self.game, {'action': 'play', 'card_id': CARD,
            'expected_version': '2', 'after_version': '1', 'actor': RIVAL}).status_code, 200)
        self.assertEqual(remote.call_args.kwargs['expected_version'], 2)
        self.assertNotIn('actor', remote.call_args.kwargs)

    @patch('apps.plans.uno_duel_views.uno_duel.game', side_effect=PermissionDenied)
    def test_unauthorized_game_is_not_visible(self, remote):
        self.assertEqual(self.client.get(self.game).status_code, 403)

    @override_settings(UNO_MULTIPLAYER_ENABLED=False)
    def test_feature_is_off_by_default(self):
        self.assertEqual(self.client.get(self.lobby).status_code, 404)
        self.assertEqual(self.client.get(self.game).status_code, 404)

    @override_settings(UNO_SUPABASE_ENABLED=False)
    def test_duel_requires_persistent_uno_scores(self):
        self.assertEqual(self.client.get(self.lobby).status_code, 404)
