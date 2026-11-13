from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings

from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row, proposal_row


class UnoViewsTests(TestCase):
    def setUp(self):
        self.current = patch("apps.users.services.session_service.current_user",
                             return_value={"id": USER_ID, "username": "Ana"})
        self.token = patch("apps.users.services.session_service.get_access_token", return_value="private-jwt")
        self.plan = patch("apps.plans.views._plan_and_group", return_value=(
            plan_row(election_method="combat", status=True, selected_proposal_id=None),
            {"id": GROUP_ID, "role": "member"},
        ))
        self.proposal = patch("apps.plans.uno_views.proposal_service.get_proposal",
                              return_value=proposal_row(created_by=USER_ID))
        self.plan_mock = self.plan.start()
        self.proposal_mock = self.proposal.start()
        for mock in (self.current, self.token):
            mock.start()
        for mock in (self.current, self.token, self.plan, self.proposal):
            self.addCleanup(mock.stop)
        self.url = f"/planes/{PLAN_ID}/propuestas/47/uno/"

    def test_start_and_restore_do_not_expose_hidden_cards(self):
        self.assertIsNone(self.client.get(self.url).json()["game"])
        response = self.client.post(self.url, {"action": "start"})
        self.assertEqual(response.status_code, 200)
        game = response.json()["game"]
        self.assertEqual((len(game["hand"]), game["bot_count"]), (7, 7))
        self.assertNotIn("deck", game)
        self.assertNotIn("bot", game)
        self.assertEqual(self.client.get(self.url).json()["game"]["id"], game["id"])

    def test_invalid_card_is_rejected_without_changing_hand(self):
        before = self.client.post(self.url, {"action": "start"}).json()["game"]
        response = self.client.post(self.url, {"action": "play", "card_id": "not-a-card"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["game"]["hand"], before["hand"])

    def test_cannot_play_for_someone_elses_proposal(self):
        self.proposal_mock.return_value = proposal_row(created_by="e0c9b706-c892-4f13-8432-86ce10caf447")
        self.assertEqual(self.client.post(self.url, {"action": "start"}).status_code, 403)

    def test_confirmed_plan_cannot_start_a_game(self):
        self.plan_mock.return_value[0]["selected_proposal_id"] = 47
        self.assertEqual(self.client.post(self.url, {"action": "start"}).status_code, 403)

    def test_an_action_requires_a_game(self):
        self.assertEqual(self.client.post(self.url, {"action": "draw"}).status_code, 409)

    def test_partida_is_private_to_the_browser_session(self):
        self.client.post(self.url, {"action": "start"})
        self.assertIsNone(Client().get(self.url).json()["game"])

    def test_a_valid_move_updates_the_public_state(self):
        before = self.client.post(self.url, {"action": "start"}).json()["game"]
        playable = [card for card in before["hand"] if card["playable"]]
        if playable:
            chosen = playable[0]
            response = self.client.post(self.url, {
                "action": "play", "card_id": chosen["id"], "color": "rojo",
            })
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(chosen["id"], [card["id"] for card in response.json()["game"]["hand"]])
        else:
            response = self.client.post(self.url, {"action": "draw"})
            self.assertEqual(response.status_code, 200)
        self.assertNotIn("bot", response.json()["game"])
        self.assertNotIn("deck", response.json()["game"])

    @override_settings(UNO_SUPABASE_ENABLED=True)
    @patch("apps.plans.uno_views.uno_supabase.game", return_value={"game": None, "victories": 3})
    def test_remote_engine_is_used_only_when_explicitly_enabled(self, remote_game):
        response = self.client.post(self.url, {"action": "start"})
        self.assertEqual(response.json(), {"game": None, "victories": 3})
        self.assertEqual(remote_game.call_args.kwargs["action"], "start")
        self.assertEqual(remote_game.call_args.args[0], "private-jwt")
        self.plan_mock.assert_not_called()
        self.proposal_mock.assert_not_called()
        self.assertFalse(any(key.startswith("uno:") for key in self.client.session.keys()))

    @override_settings(UNO_SUPABASE_ENABLED=True)
    @patch("apps.plans.uno_views.uno_supabase.game", side_effect=PermissionDenied)
    def test_remote_engine_denial_does_not_fall_back_to_local_game(self, remote_game):
        response = self.client.post(self.url, {"action": "start"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(any(key.startswith("uno:") for key in self.client.session.keys()))
