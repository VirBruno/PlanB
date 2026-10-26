"""Contrato real del SDK de Supabase para la consulta mensual, con HTTP en memoria."""
from datetime import date
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from apps.plans.services import plan_service
from apps.plans.services.exceptions import PlanUnavailable
from apps.plans.tests.test_services import plan_row
from apps.users.calendar_month import month_bounds
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired


class MeetingQueryTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.responses, self.transports = [], [], []
        self.start, self.end = month_bounds(date(2026, 10, 1))

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

        self.transport_patch = patch.object(clients, "http_client", side_effect=factory)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(transport.is_closed for transport in self.transports))
        for request in self.requests:
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def query(self, token="user-jwt"):
        return plan_service.list_meetings(token, start=self.start, end=self.end)

    def row(self, **changes):
        return plan_row(**{
            "selected_proposal_id": 47, "confirmed_at": "2026-10-22T18:00:00Z",
            "scheduled_at": "2026-10-26T18:30:00Z", **changes,
        })

    def test_only_selected_proposals_in_local_date_interval_without_status_filter(self):
        self.responses = [(200, [self.row(), self.row(status=False)])]
        plans = self.query()
        self.assertEqual([plan["status"] for plan in plans], [True, False])
        self.assertEqual(plans[0]["selected_proposal_id"], 47)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url.path, "/rest/v1/plans")
        self.assertEqual(request.url.params["selected_proposal_id"], "not.is.null")
        self.assertEqual(request.url.params.get_list("scheduled_at"),
                         ["gte." + self.start.isoformat(), "lt." + self.end.isoformat()])
        self.assertNotIn("status", request.url.params)
        self.assertNotIn("created_by", request.url.params)
        self.assertEqual(request.url.params["order"], "scheduled_at.asc,id.asc")
        self.assertIn("selected_proposal_id", request.url.params["select"])
        self.assertIn("scheduled_at", request.url.params["select"])

    @patch.object(plan_service, "MEETING_PAGE_SIZE", 2)
    def test_fetches_all_pages_not_just_first_page_of_plans(self):
        self.responses = [(200, [self.row(), self.row()]), (200, [self.row()])]
        self.assertEqual(len(self.query()), 3)
        self.assertEqual([request.url.params["offset"] for request in self.requests], ["0", "2"])
        self.assertEqual([request.url.params["limit"] for request in self.requests], ["2", "2"])

    def test_empty_result(self):
        self.responses = [(200, [])]
        self.assertEqual(self.query(), [])

    def test_missing_decision_schema_does_not_fall_back_to_tentative_dates(self):
        self.responses = [(400, {
            "code": "42703", "message": "column selected_proposal_id does not exist",
            "details": None, "hint": None,
        })]
        with self.assertRaises(PlanUnavailable):
            self.query()
        self.assertEqual(len(self.requests), 1)

    def test_invalid_provider_data_is_unavailable(self):
        self.responses = [(200, [self.row(scheduled_at="incorrecta")])]
        with self.assertRaises(PlanUnavailable):
            self.query()

    def test_expired_jwt_is_propagated(self):
        self.responses = [(401, {
            "code": "PGRST301", "message": "expired", "details": None, "hint": None,
        })]
        with self.assertRaises(SessionExpired):
            self.query()

    def test_no_token_does_not_open_connection(self):
        with self.assertRaises(SessionExpired):
            self.query("")
        self.assertFalse(self.requests)
