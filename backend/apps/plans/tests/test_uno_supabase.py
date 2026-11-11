from contextlib import contextmanager
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase
from postgrest.exceptions import APIError

from apps.plans.services import uno_game, uno_supabase
from apps.plans.services.proposal_exceptions import InvalidProposal, ProposalUnavailable
from apps.plans.tests.test_services import PLAN_ID


class UnoSupabaseTests(SimpleTestCase):
    def setUp(self):
        self.client = Mock()
        self.result = self.client.rpc.return_value.execute.return_value

        @contextmanager
        def public_client(*, access_token):
            self.assertEqual(access_token, "jwt-del-integrante")
            yield self.client

        self.patcher = patch("apps.plans.services.uno_supabase.clients.public_client", public_client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def call(self, **kwargs):
        return uno_supabase.game("jwt-del-integrante", plan_id=PLAN_ID, proposal_id=47, **kwargs)

    def test_uses_member_jwt_and_never_returns_hidden_data(self):
        game = uno_game.public_state(uno_game.new_game())
        game["deck"] = [{"secret": "orden"}]
        game["bot"] = [{"secret": "mano"}]
        game["hand"][0]["secret"] = "dato extra"
        self.result.data = {"game": game, "victories": 2, "extra": "oculto"}
        response = self.call(action="start")
        self.assertEqual(response["victories"], 2)
        self.assertNotIn("deck", response["game"])
        self.assertNotIn("bot", response["game"])
        self.assertNotIn("secret", response["game"]["hand"][0])
        self.client.rpc.assert_called_once_with("uno_bot_game", {
            "p_plan_id": PLAN_ID, "p_proposal_id": 47, "p_action": "start",
            "p_card_id": None, "p_color": None,
        })

    def test_accepts_no_game_and_sends_card_action(self):
        self.result.data = {"game": None, "victories": 0}
        card_id = str(uuid4())
        self.assertIsNone(self.call(action="play", card_id=card_id, color="rojo")["game"])
        self.assertEqual(self.client.rpc.call_args.args[1]["p_card_id"], card_id)

    def test_rejects_unknown_action_before_call(self):
        with self.assertRaises(InvalidProposal):
            self.call(action="victoria")
        self.client.rpc.assert_not_called()

    def test_rejects_invalid_card_response(self):
        game = uno_game.public_state(uno_game.new_game())
        game["hand"][0]["color"] = "inexistente"
        self.result.data = {"game": game, "victories": 0}
        with self.assertRaises(ProposalUnavailable):
            self.call(action="status")

    def test_translates_permission_and_database_failure(self):
        self.client.rpc.side_effect = APIError({"code": "42501", "message": "denegado"})
        with self.assertRaises(PermissionDenied):
            self.call(action="status")
        self.client.rpc.side_effect = RuntimeError("red desconectada")
        with self.assertRaises(ProposalUnavailable):
            self.call(action="status")

    def test_scores_reject_duplicate_proposal_rows(self):
        self.result.data = [{"proposal_id": 47, "victories": 1}, {"proposal_id": 47, "victories": 2}]
        with self.assertRaises(ProposalUnavailable):
            uno_supabase.scores("jwt-del-integrante", plan_id=PLAN_ID)

    def test_scores_returns_victories_for_each_proposal(self):
        self.result.data = [{"proposal_id": 47, "victories": 2}, {"proposal_id": 48, "victories": 0}]
        self.assertEqual(uno_supabase.scores("jwt-del-integrante", plan_id=PLAN_ID), {47: 2, 48: 0})
        self.client.rpc.assert_called_once_with("uno_proposal_scores", {"p_plan_id": PLAN_ID})

    def test_only_public_event_fields_are_forwarded(self):
        game = uno_game.public_state(uno_game.new_game())
        game["events"] = [{"type": "draw", "actor": "player", "count": 2,
                           "reason": "mas_dos", "hidden_card": "secreto"}]
        self.result.data = {"game": game, "victories": 0}
        events = self.call(action="status")["game"]["events"]
        self.assertEqual(events, [{"type": "draw", "actor": "player", "count": 2, "reason": "mas_dos"}])

    def test_bot_play_event_retains_public_card_color(self):
        game = uno_game.public_state(uno_game.new_game())
        game["events"] = [{"type": "play", "actor": "bot", "value": "salto", "color": "azul", "id": "privado"}]
        self.result.data = {"game": game, "victories": 0}
        self.assertEqual(self.call(action="status")["game"]["events"], [
            {"type": "play", "actor": "bot", "value": "salto", "color": "azul"},
        ])
