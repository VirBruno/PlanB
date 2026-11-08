"""Prompt 4: inputs, summary y orquestación real con RPC/proveedores simulados."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, local
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from apps.plans.services import plan_recommendation_service as service
from apps.plans.services.place_intent_service import validate_intents
from apps.plans.services.place_types import RecommendationError
from apps.plans.services.recommendation_inputs import activity_payload, input_fingerprint
from apps.plans.tests.test_recommendation_claim import AtomicClaimFixture
from apps.plans.tests.test_recommendations import candidate, plan
from apps.users.services import clients


class RecommendationInputTests(SimpleTestCase):
    def setUp(self):
        self.proposal = {"tittle": "Café", "description": "Con amigos", "latitude": -34.6, "longitude": -58.38}

    def test_fingerprint_is_deterministic_and_independent_of_key_order(self):
        self.assertEqual(input_fingerprint(plan(), self.proposal),
                         input_fingerprint(plan(), dict(reversed(list(self.proposal.items())))))
        self.assertRegex(input_fingerprint(plan(), self.proposal), r"^[0-9a-f]{64}$")

    def test_equivalent_text_unicode_whitespace_and_coordinates_are_normalized(self):
        first = {**self.proposal, "tittle": " Cafe\u0301  ", "description": "\nCon   amigos\t", "latitude": 1}
        second = {**self.proposal, "tittle": "Café", "latitude": 1.0}
        self.assertEqual(input_fingerprint(plan(), first), input_fingerprint(plan(), second))
        self.assertEqual(input_fingerprint(plan(), {**first, "latitude": -0.0}),
                         input_fingerprint(plan(), {**first, "latitude": 0.0}))

    def test_each_consumed_input_changes_fingerprint(self):
        original = input_fingerprint(plan(), self.proposal)
        for field, value in (("tittle", "Cena"), ("description", "Otro contexto"),
                             ("latitude", -34.7), ("longitude", -58.4)):
            with self.subTest(field=field):
                self.assertNotEqual(original, input_fingerprint(plan(), {**self.proposal, field: value}))
        self.assertNotEqual(original, input_fingerprint(plan(selected_proposal_id=48), self.proposal))

    def test_irrelevant_inputs_and_private_data_do_not_change_fingerprint(self):
        changed = {**self.proposal, "votes": 9, "Score": 300, "budget_min": 50, "created_by": "private",
                   "type": "salida", "date_pick": "otro día", "members": ["private"], "avatar": "image"}
        self.assertEqual(input_fingerprint(plan(), self.proposal),
                         input_fingerprint(plan(name="Otro título", description="Otros gastos"), changed))

    def test_only_text_consumed_by_gemini_affects_fingerprint(self):
        first = {**self.proposal, "description": "d" * 4000 + "fuera del límite"}
        second = {**self.proposal, "description": "d" * 4000 + "diferente"}
        self.assertEqual(input_fingerprint(plan(), first), input_fingerprint(plan(), second))
        self.assertEqual(activity_payload(first), {"title": "Café", "description": "d" * 4000})


class RecommendationUpdateTests(SimpleTestCase):
    def setUp(self):
        self.remote = AtomicClaimFixture()
        self.proposal = {"tittle": "Merienda", "description": "Con amigos", "latitude": -34.6, "longitude": -58.38}
        self.remote.saved["criteria"]["input_fingerprint"] = input_fingerprint(self.remote.plan, self.proposal)
        self.remote.saved["criteria"]["summary"] = "Para este plan buscamos cafeterías cerca del punto acordado."
        self.transports = []

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(self.remote.handle))
            self.transports.append(transport)
            return transport

        patches = {
            "plan_service.get_plan": {"side_effect": lambda *a: deepcopy(self.remote.plan)},
            "group_service.get_group": {"return_value": {"role": "member"}},
            "proposal_service.get_proposal": {"side_effect": lambda *a, **k: deepcopy(self.proposal)},
            "recommendation_store.read": {"side_effect": lambda *a: deepcopy(self.remote.saved)},
            "GeminiIntentClient": {},
            "GeoapifyClient.search": {"return_value": [candidate("new", categories=["catering.restaurant"])]},
            "GeoapifyClient.geocode": {},
        }
        self.mocks = {}
        for target, kwargs in patches.items():
            patcher = patch(f"apps.plans.services.plan_recommendation_service.{target}", **kwargs)
            self.mocks[target] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(clients, "http_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.interpret = self.mocks["GeminiIntentClient"].return_value.interpret
        self.interpret.return_value = validate_intents({"place_intents": ["restaurant"],
            "summary": "Para este plan buscamos restaurantes cerca del punto acordado."}, model="gemini-configured-model")

    def tearDown(self):
        self.assertTrue(all(client.is_closed for client in self.transports))

    def generate(self, **kwargs):
        return service.generate_recommendations("private-jwt", self.remote.plan["id"], **kwargs)

    def read(self):
        return service.get_recommendations("private-jwt", self.remote.plan["id"])

    def stale(self):
        self.proposal["tittle"] = "Cena"

    def test_unchanged_current_snapshot_reuses_everything_without_external_requests(self):
        before = deepcopy(self.remote.saved)
        self.assertFalse(self.read()["is_stale"])
        self.assertEqual(self.generate(), self.read())
        self.assertEqual(self.remote.saved, before)
        self.assertEqual(self.remote.requests, [])
        self.interpret.assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()

    def test_each_relevant_change_is_stale_but_get_never_generates(self):
        for field, value in (("tittle", "Cena"), ("description", "Nueva actividad"),
                             ("latitude", -34.7), ("longitude", -58.4)):
            previous = self.proposal[field]
            self.proposal[field] = value
            with self.subTest(field=field):
                result = self.read()
                self.assertTrue(result["is_stale"])
                self.assertTrue(result["can_update"])
                self.assertTrue(result["has_snapshot"])
                self.assertEqual(result["items"][0]["provider_place_id"], "poi1")
            self.proposal[field] = previous
        self.interpret.assert_not_called()
        self.assertEqual(self.remote.requests, [])

    def test_irrelevant_change_does_not_generate(self):
        self.proposal.update(votes=99, budget_min=300, date_pick="otra fecha", type="salida")
        self.assertFalse(self.generate()["can_update"])
        self.interpret.assert_not_called()

    def test_stale_generation_replaces_items_summary_and_fingerprint(self):
        self.stale()
        old_generated_at = self.remote.saved["generated_at"]
        self.remote.now = self.remote.now.replace(minute=10)
        result = self.generate()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["is_stale"])
        self.assertFalse(result["can_update"])
        self.assertEqual(result["items"][0]["provider_place_id"], "new")
        self.assertEqual(result["ai_summary"], self.interpret.return_value.summary)
        self.assertNotEqual(result["generated_at"], old_generated_at)
        self.assertEqual(self.remote.saved["criteria"]["input_fingerprint"], input_fingerprint(self.remote.plan, self.proposal))
        self.generate()
        self.read()
        self.interpret.assert_called_once()
        self.mocks["GeoapifyClient.search"].assert_called_once()

    def test_gemini_failure_preserves_snapshot_and_suppresses_automatic_repeats(self):
        before = deepcopy(self.remote.saved)
        self.stale()
        self.interpret.side_effect = RecommendationError("timeout")
        first = self.generate()
        second = self.generate()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["ai_summary"], before["criteria"]["summary"])
        self.assertTrue(first["has_snapshot"])
        self.assertFalse(first["can_update"])
        for field in ("items", "base_location", "generated_at"):
            self.assertEqual(self.remote.saved[field], before[field])
        self.interpret.assert_called_once()
        self.mocks["GeoapifyClient.search"].assert_not_called()

    def test_places_failure_preserves_snapshot_and_summary(self):
        self.stale()
        before = deepcopy(self.remote.saved)
        self.mocks["GeoapifyClient.search"].side_effect = RecommendationError("http_error")
        result = self.generate()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["ai_summary"], before["criteria"]["summary"])
        self.assertEqual(self.remote.saved["items"], before["items"])
        self.assertEqual(self.remote.saved["criteria"]["input_fingerprint"], before["criteria"]["input_fingerprint"])

    def test_failed_update_explicit_retry_can_succeed_without_reconfirmation(self):
        self.stale()
        self.interpret.side_effect = RecommendationError("timeout")
        self.generate()
        before_plan = deepcopy(self.remote.plan)
        self.interpret.side_effect = None
        result = self.generate(replace=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.remote.plan, before_plan)
        self.assertEqual(self.interpret.call_count, 2)
        self.mocks["GeoapifyClient.search"].assert_called_once()

    def test_new_inputs_after_failure_can_generate_once_automatically(self):
        self.stale()
        self.interpret.side_effect = RecommendationError("timeout")
        self.generate()
        self.proposal["description"] = "Otro contexto"
        self.assertTrue(self.read()["can_update"])
        self.interpret.side_effect = None
        self.assertEqual(self.generate()["status"], "ready")
        self.assertEqual(self.interpret.call_count, 2)

    def test_no_previous_snapshot_retains_initial_error_without_invented_items(self):
        self.remote.saved = None
        self.interpret.side_effect = RecommendationError("timeout")
        result = self.generate()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["has_snapshot"])
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["ai_summary"])
        self.assertFalse(result["can_update"])
        self.generate()
        self.interpret.assert_called_once()

    def test_bad_summary_still_generates_valid_places(self):
        self.stale()
        self.interpret.return_value = validate_intents({"place_intents": ["restaurant"],
            "summary": "<script>secret</script>"}, model="gemini-configured-model")
        result = self.generate()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNone(result["ai_summary"])

    def test_public_contract_hides_criteria_fingerprints_and_model(self):
        self.stale()
        result = self.generate()
        for field in ("criteria", "input_fingerprint", "attempted_input_fingerprint", "model", "place_intents"):
            self.assertNotIn(field, result)
        self.assertNotIn("gemini-configured-model", str(result))
        self.assertNotIn("private-jwt", str(self.remote.saved))
        self.assertNotIn("Cena", str(self.remote.saved))

    def test_legacy_ready_reuses_but_explicit_refresh_can_upgrade(self):
        self.remote.saved["criteria"].pop("input_fingerprint")
        result = self.generate()
        self.assertTrue(result["can_refresh"])
        self.interpret.assert_not_called()
        self.assertEqual(self.generate(replace=True)["status"], "ready")
        self.assertIn("input_fingerprint", self.remote.saved["criteria"])
        self.assertFalse(self.read()["can_refresh"])
        self.interpret.assert_called_once()

    def test_pending_update_serves_previous_items_and_summary_without_generation(self):
        self.stale()
        from apps.plans.services import recommendation_store
        recommendation_store.claim(self.remote.plan, input_fingerprint=input_fingerprint(self.remote.plan, self.proposal))
        result = self.generate()
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["has_snapshot"])
        self.assertEqual(result["items"][0]["provider_place_id"], "poi1")
        self.assertIsNotNone(result["ai_summary"])
        self.interpret.assert_not_called()

    def test_two_members_with_same_stale_read_have_one_effective_generation(self):
        self.stale()
        barrier, thread_state = Barrier(2), local()

        def read(*args):
            snapshot = deepcopy(self.remote.saved)
            if not getattr(thread_state, "has_read", False):
                thread_state.has_read = True
                barrier.wait(timeout=10)
            return snapshot

        self.mocks["recommendation_store.read"].side_effect = read
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.generate) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertTrue(all(result["status"] in ("ready", "pending") for result in results))
        self.interpret.assert_called_once()
        self.mocks["GeoapifyClient.search"].assert_called_once()
