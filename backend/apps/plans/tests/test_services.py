"""Ejecuta el SDK real de Supabase sobre un transporte HTTP en memoria."""
import json
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from apps.plans.services import plan_service
from apps.plans.services.exceptions import PlanNotFound, PlanUnavailable
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

PLAN_ID = "7139c649-18c5-4743-b18d-c891c461e80d"
USER_ID = "87c6ef36-64c5-4e6e-b37c-b760762d82b9"
GROUP_ID = "9f3bb3a8-50ea-4f9d-a7c5-80e5ed548e8e"


def plan_row(**kwargs):
    return {
        "id": PLAN_ID, "created_at": "2026-09-30T12:00:00Z",
        "name": "Escapada", "description": "Viaje compartido",
        "status": True, "group_id": GROUP_ID, "created_by": USER_ID,
        **kwargs,
    }


class PlanSDKTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.responses, self.transports = [], [], []

        def handle(request):
            self.requests.append(request)
            if not self.responses:
                raise AssertionError("HTTP inesperado")
            status, payload = self.responses.pop(0)
            return httpx.Response(status, json=payload)

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(handle))
            self.transports.append(transport)
            return transport

        self.patch = patch.object(clients, "http_client", side_effect=factory)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(transport.is_closed for transport in self.transports))
        for request in self.requests:
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def test_list_uses_stable_pagination_and_validates_rows(self):
        self.responses = [(200, [plan_row()] * 13)]
        page = plan_service.list_plans("user-jwt", page=2, group_id=GROUP_ID)
        self.assertEqual(len(page["items"]), 12)
        self.assertTrue(page["has_next"])
        self.assertEqual(page["previous_page"], 1)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/rest/v1/plans")
        self.assertEqual(request.url.params["order"], "created_at.desc,id.desc")
        self.assertEqual(request.url.params["offset"], "12")
        self.assertEqual(request.url.params["group_id"], "eq." + GROUP_ID)

    def test_create_sets_actor_and_only_submits_plan_fields(self):
        self.responses = [(201, [{"id": PLAN_ID}])]
        self.assertEqual(plan_service.create_plan(
            "user-jwt", created_by=USER_ID, name="Escapada",
            description="Viaje", status=True, group_id=GROUP_ID,
        ), PLAN_ID)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/rest/v1/plans")
        self.assertEqual(json.loads(request.content), {
            "name": "Escapada", "description": "Viaje", "status": True,
            "group_id": GROUP_ID, "created_by": USER_ID,
        })

    def test_update_filters_by_plan_and_leaves_group_ownership_to_rls(self):
        self.responses = [(200, [{"id": PLAN_ID}])]
        self.assertEqual(plan_service.update_plan(
            "user-jwt", plan_id=PLAN_ID,
            name="Nuevo", description="Detalle", status=False,
        ), PLAN_ID)
        request = self.requests[0]
        self.assertEqual(request.method, "PATCH")
        self.assertEqual(request.url.params["id"], "eq." + PLAN_ID)
        self.assertNotIn("created_by", json.loads(request.content))
        self.assertNotIn("group_id", json.loads(request.content))

    def test_delete_filters_by_plan_and_leaves_group_ownership_to_rls(self):
        self.responses = [(200, [{"id": PLAN_ID}])]
        self.assertEqual(plan_service.delete_plan("user-jwt", PLAN_ID), PLAN_ID)
        request = self.requests[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(request.url.params["id"], "eq." + PLAN_ID)

    def test_missing_or_malformed_data_is_controlled(self):
        self.responses = [(200, [])]
        with self.assertRaises(PlanNotFound):
            plan_service.get_plan("user-jwt", PLAN_ID)
        self.responses = [(200, [plan_row(status="true")])]
        with self.assertRaises(PlanUnavailable):
            plan_service.list_plans("user-jwt")

    def test_rls_permission_denial_is_service_unavailable_not_form_validation(self):
        self.responses = [(403, {"code": "42501", "message": "permission denied"})]
        with self.assertRaises(PlanUnavailable):
            plan_service.create_plan(
                "user-jwt", created_by=USER_ID, group_id=GROUP_ID,
                name="Plan", description="Descripción", status=True,
            )

    def test_missing_token_and_invalid_uuid_never_call_provider(self):
        with self.assertRaises(SessionExpired):
            plan_service.list_plans("")
        with self.assertRaises(PlanNotFound):
            plan_service.get_plan("user-jwt", "invalid")
        with self.assertRaises(PlanNotFound):
            plan_service.delete_plan("user-jwt", plan_id="invalid")
        self.assertFalse(self.requests)