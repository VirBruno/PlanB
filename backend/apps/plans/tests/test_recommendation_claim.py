"""Contrato claim por HTTPX MockTransport, sin PostgreSQL ni proveedores reales.

La máquina de estados simula la atomicidad de la RPC. Estas pruebas verifican
el adaptador SDK y consumidores concurrentes contra ese contrato, no ejecutan SQL.
La prueba SQL manual complementaria valida la implementación en PostgreSQL.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier, Event, Lock
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from django.test import SimpleTestCase

from apps.plans.services import recommendation_store as store
from apps.plans.tests.test_recommendations import candidate, plan, row
from apps.users.services import clients

A, B, C = "a" * 64, "b" * 64, "c" * 64


class AtomicClaimFixture:
    """Modelo del contrato: el lock pertenece sólo al servidor RPC simulado."""
    def __init__(self):
        self.lock = Lock()
        self.now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        self.plan = plan()
        self.saved = row(items=[{**candidate(), "rank": 1}], criteria={
            "input_fingerprint": A, "summary": "Texto anterior.", "categories": ["catering.cafe"],
        }, base_location={"latitude": -34.6, "longitude": -58.38},
            generated_at=self.now.isoformat(), updated_at=self.now.isoformat(), generation_id=str(uuid4()))
        self.requests = []

    def handle(self, request):
        with self.lock:
            payload = json.loads(request.content)
            self.requests.append((request.url.path, payload, dict(request.headers)))
            if request.url.path == "/rest/v1/rpc/claim_plan_recommendations":
                result = self.claim(payload)
            elif request.url.path == "/rest/v1/rpc/finish_plan_recommendations":
                result = self.finish(payload)
            else:
                raise AssertionError("RPC inesperada")
            return httpx.Response(200, content=json.dumps(result), headers={"Content-Type": "application/json"})

    def claim(self, payload):
        if (payload["p_plan_id"] != self.plan["id"] or not self.plan["status"]
                or payload["p_proposal_id"] != self.plan["selected_proposal_id"]
                or payload["p_confirmed_at"] != self.plan["confirmed_at"].isoformat()):
            return None
        fingerprint = payload.get("p_input_fingerprint")
        replace = payload["p_replace"] is True
        if self.saved is not None:
            if (self.saved["status"] == "pending"
                    and datetime.fromisoformat(self.saved["updated_at"]) > self.now - timedelta(minutes=2)):
                return None
            criteria = self.saved.get("criteria")
            criteria = criteria if isinstance(criteria, dict) else {}
            if fingerprint is None:
                if not replace:
                    return None
            else:
                if (criteria.get("input_fingerprint") == fingerprint
                        or self.saved["status"] == "ready" and criteria.get("input_fingerprint") is None
                        and not replace):
                    return None
                if criteria.get("attempted_input_fingerprint") == fingerprint and not replace:
                    return None
                self.saved["criteria"] = {**criteria, "attempted_input_fingerprint": fingerprint}
            self.saved.update(status="pending", error_code=None, generation_id=str(uuid4()),
                              updated_at=self.now.isoformat())
        else:
            self.saved = row(status="pending", generation_id=str(uuid4()), updated_at=self.now.isoformat(),
                             criteria={"attempted_input_fingerprint": fingerprint} if fingerprint else None)
        return self.saved["generation_id"]

    def finish(self, payload):
        if (self.saved is None or self.saved["status"] != "pending"
                or self.saved["generation_id"] != payload["p_generation_id"]):
            return False
        self.saved.update(status="failed" if payload["p_error_code"] else "ready",
                          error_code=payload["p_error_code"])
        if not payload["p_error_code"]:
            self.saved.update(items=deepcopy(payload["p_items"]), criteria=deepcopy(payload["p_criteria"]),
                              base_location=deepcopy(payload["p_base_location"]),
                              generated_at=self.now.isoformat())
        return True


class FingerprintClaimContractTests(SimpleTestCase):
    def setUp(self):
        self.remote = AtomicClaimFixture()
        self.transports = []

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(self.remote.handle))
            self.transports.append(transport)
            return transport

        patcher = patch.object(clients, "http_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertTrue(all(client.is_closed for client in self.transports))

    def claim(self, fingerprint=B, *, replace=False):
        return store.claim(self.remote.plan, replace=replace, input_fingerprint=fingerprint)

    def finish(self, ticket, *, error=None, fingerprint=B):
        return store.finish(self.remote.plan, ticket, items=[{**candidate("new"), "rank": 1}],
                            error_code=error, base_location={"latitude": -34.6, "longitude": -58.38},
                            criteria={"input_fingerprint": fingerprint, "summary": "Texto nuevo."})

    def test_sdk_sends_fingerprint_only_to_backend_claim(self):
        self.assertIsNotNone(self.claim())
        path, payload, headers = self.remote.requests[0]
        self.assertEqual(path, "/rest/v1/rpc/claim_plan_recommendations")
        self.assertEqual(payload, {
            "p_plan_id": self.remote.plan["id"], "p_proposal_id": self.remote.plan["selected_proposal_id"],
            "p_confirmed_at": self.remote.plan["confirmed_at"].isoformat(),
            "p_replace": False, "p_input_fingerprint": B,
        })
        self.assertEqual(headers["apikey"], "sb_secret_test_placeholder")
        self.assertNotIn("authorization", headers)

    def test_legacy_call_keeps_four_argument_contract(self):
        self.assertIsNotNone(store.claim(self.remote.plan, replace=True))
        self.assertNotIn("p_input_fingerprint", self.remote.requests[0][1])
        self.assertNotIn("attempted_input_fingerprint", self.remote.saved["criteria"])

    def test_invalid_fingerprint_is_rejected_before_network(self):
        for value in ("", "a" * 63, "a" * 65, "A" * 64, "not-a-hash", 123, {}, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.claim(value)
        self.assertEqual(self.remote.requests, [])

    def test_current_ready_snapshot_is_unchanged_even_with_explicit_replace(self):
        before = deepcopy(self.remote.saved)
        self.assertIsNone(self.claim(A))
        self.assertIsNone(self.claim(A, replace=True))
        self.assertEqual(self.remote.saved, before)

    def test_legacy_ready_does_not_trigger_mass_regeneration(self):
        for criteria in (None, {}, {"summary": "Legacy"}):
            self.remote.saved["criteria"] = criteria
            before = deepcopy(self.remote.saved)
            self.assertIsNone(self.claim(B))
            self.assertEqual(self.remote.saved, before)

    def test_legacy_ready_explicit_refresh_records_fingerprint_and_preserves_snapshot(self):
        self.remote.saved["criteria"] = {"summary": "Legacy"}
        before = deepcopy(self.remote.saved)
        ticket = self.claim(B, replace=True)
        self.assertIsNotNone(ticket)
        self.assertEqual(self.remote.saved["criteria"], {
            "summary": "Legacy", "attempted_input_fingerprint": B,
        })
        for field in ("items", "base_location", "generated_at"):
            self.assertEqual(self.remote.saved[field], before[field])
        self.assertTrue(self.finish(ticket))
        self.assertEqual(self.remote.saved["criteria"]["input_fingerprint"], B)
        self.assertIsNone(self.claim(B, replace=True))

    def test_claim_records_attempt_and_keeps_previous_snapshot(self):
        before = deepcopy(self.remote.saved)
        self.assertIsNotNone(self.claim())
        self.assertEqual(self.remote.saved["status"], "pending")
        for field in ("items", "base_location", "generated_at"):
            self.assertEqual(self.remote.saved[field], before[field])
        self.assertEqual(self.remote.saved["criteria"], {**before["criteria"], "attempted_input_fingerprint": B})

    def test_failure_keeps_snapshot_summary_fingerprint_and_attempt(self):
        before = deepcopy(self.remote.saved)
        ticket = self.claim()
        self.assertTrue(self.finish(ticket, error="timeout"))
        self.assertEqual(self.remote.saved["status"], "failed")
        for field in ("items", "base_location", "generated_at"):
            self.assertEqual(self.remote.saved[field], before[field])
        self.assertEqual(self.remote.saved["criteria"], {**before["criteria"], "attempted_input_fingerprint": B})
        self.assertIsNone(self.claim())

    def test_new_fingerprint_can_claim_after_failed_previous_attempt(self):
        self.finish(self.claim(), error="http_error")
        self.assertIsNotNone(self.claim(C))
        self.assertEqual(self.remote.saved["criteria"]["attempted_input_fingerprint"], C)

    def test_explicit_retry_is_allowed_but_cannot_steal_recent_pending(self):
        ticket = self.claim()
        self.assertIsNone(self.claim(C))
        self.assertIsNone(self.claim(replace=True))
        self.finish(ticket, error="timeout")
        self.assertIsNotNone(self.claim(replace=True))
        self.assertIsNone(self.claim(replace=True))

    def test_expired_pending_requires_explicit_retry_for_same_fingerprint(self):
        old_ticket = self.claim()
        self.remote.saved["updated_at"] = (self.remote.now - timedelta(minutes=3)).isoformat()
        self.assertIsNone(self.claim())
        new_ticket = self.claim(replace=True)
        self.assertIsNotNone(new_ticket)
        self.assertNotEqual(new_ticket, old_ticket)
        self.assertFalse(self.finish(old_ticket))
        self.assertTrue(self.finish(new_ticket))

    def test_initial_generation_records_attempt_without_previous_snapshot(self):
        self.remote.saved = None
        ticket = self.claim(A)
        self.assertIsNotNone(ticket)
        self.assertEqual(self.remote.saved["items"], [])
        self.assertEqual(self.remote.saved["criteria"], {"attempted_input_fingerprint": A})
        self.finish(ticket, error="timeout", fingerprint=A)
        self.assertIsNone(self.claim(A))

    def concurrent_workers(self, *, late=False, failure=False):
        barrier, reserved, completed, second_attempted = Barrier(2), Event(), Event(), Event()
        gemini, places = Mock(), Mock()

        def worker(index):
            # Ambos miembros observaron el mismo ready anterior antes de reservar.
            observed = deepcopy(self.remote.saved)
            self.assertEqual(observed["criteria"]["input_fingerprint"], A)
            barrier.wait(timeout=10)
            if index:
                self.assertTrue(reserved.wait(10))
                if late:
                    self.assertTrue(completed.wait(10))
                try:
                    return self.claim()
                finally:
                    second_attempted.set()
            ticket = self.claim()
            self.assertIsNotNone(ticket)
            reserved.set()
            gemini()
            if not failure:
                places()
            if not late:
                self.assertTrue(second_attempted.wait(10))
            self.finish(ticket, error="timeout" if failure else None)
            completed.set()
            return ticket

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, i) for i in range(2)]
            tickets = [future.result(timeout=20) for future in futures]
        self.assertIsNotNone(tickets[0])
        self.assertIsNone(tickets[1])
        gemini.assert_called_once()
        if failure:
            places.assert_not_called()
        else:
            places.assert_called_once()

    def test_two_members_while_pending_have_one_external_generation(self):
        self.concurrent_workers()

    def test_delayed_competitor_after_success_does_not_generate_again(self):
        self.concurrent_workers(late=True)

    def test_delayed_competitor_after_timeout_does_not_retry_automatically(self):
        self.concurrent_workers(late=True, failure=True)
