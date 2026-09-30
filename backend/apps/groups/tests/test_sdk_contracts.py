"""Ejecuta el SDK real; sólo se sustituye su transporte, sin red."""
import json
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from apps.groups.services import group_service
from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

GROUP_ID = "7139c649-18c5-4743-b18d-c891c461e80d"
USER_ID = "87c6ef36-64c5-4e6e-b37c-b760762d82b9"


def group_row(**kwargs):
    return {
        "id": GROUP_ID, "name": "Escapadas", "description": "Planes compartidos",
        "created_by": USER_ID, "created_at": "2026-09-30T12:00:00Z",
        "updated_at": "2026-09-30T12:00:00Z", **kwargs,
    }


class GroupSDKTests(SimpleTestCase):
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
        self.assertTrue(all(t.is_closed for t in self.transports))
        for request in self.requests:
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers["authorization"], "Bearer user-jwt")
            self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def test_create_uses_one_rpc_without_identity_or_role(self):
        self.responses = [(200, GROUP_ID)]
        self.assertEqual(group_service.create_group("user-jwt", name="Viaje", description=""), GROUP_ID)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/rest/v1/rpc/create_group")
        self.assertEqual(json.loads(request.content), {"p_name": "Viaje", "p_description": None})

    def test_list_is_by_rls_not_created_by_and_paginates(self):
        self.responses = [(200, [group_row()] * 13)]
        page = group_service.list_groups("user-jwt", page=2)
        self.assertEqual(len(page["items"]), 12)
        self.assertTrue(page["has_next"])
        self.assertEqual(page["previous_page"], 1)
        self.assertEqual(page["next_page"], 3)
        request = self.requests[0]
        self.assertNotIn("created_by", request.url.params)
        self.assertEqual(request.url.params["order"], "created_at.desc,id.desc")
        self.assertEqual(request.url.params["offset"], "12")
        self.assertEqual(request.url.params["limit"], "13")

    def test_empty_page_and_bad_page_number(self):
        for value in (0, -2, 10001, "wrong"):
            self.responses = [(200, [])]
            result = group_service.list_groups("user-jwt", page=value)
            self.assertEqual(result["page"], 1)
            self.assertFalse(result["has_next"])

    def test_detail_queries_group_and_own_membership(self):
        self.responses = [(200, [group_row()]), (200, [{"role": "member"}])]
        result = group_service.get_group("user-jwt", GROUP_ID)
        self.assertEqual(result["role_label"], "Miembro")
        self.assertEqual(result["created_at"].year, 2026)
        self.assertEqual(self.requests[0].url.params["id"], "eq." + GROUP_ID)
        self.assertEqual(self.requests[1].url.path, "/rest/v1/group_members")
        self.assertEqual(self.requests[1].url.params["select"], "role")

    def test_owner_detail(self):
        self.responses = [(200, [group_row()]), (200, [{"role": "owner"}])]
        self.assertEqual(group_service.get_group("user-jwt", GROUP_ID)["role_label"], "Owner")

    def test_inaccessible_and_missing_group_are_indistinguishable(self):
        self.responses = [(200, [])]
        with self.assertRaises(GroupNotFound):
            group_service.get_group("user-jwt", GROUP_ID)

    def test_lost_membership_does_not_expose_group(self):
        self.responses = [(200, [group_row()]), (200, [])]
        with self.assertRaises(GroupNotFound):
            group_service.get_group("user-jwt", GROUP_ID)

    def test_invalid_uuid_does_not_call_provider(self):
        with self.assertRaises(GroupNotFound):
            group_service.get_group("user-jwt", "invalid")
        self.assertFalse(self.requests)

    def test_expired_jwt_is_controlled(self):
        self.responses = [(401, {"code": "PGRST301", "message": "sensitive", "details": None, "hint": None})]
        with self.assertRaises(SessionExpired):
            group_service.list_groups("user-jwt")

    def test_sql_validation_is_controlled(self):
        self.responses = [(400, {"code": "22023", "message": "sensitive", "details": None, "hint": None})]
        with self.assertRaises(InvalidGroup) as caught:
            group_service.create_group("user-jwt", name=" ")
        self.assertNotIn("sensitive", str(caught.exception))

    def test_malformed_response_is_safe(self):
        for payload in ({"wrong": "data"}, [group_row(id="bad")], [group_row(created_at="bad")]):
            self.responses = [(200, payload)]
            with self.assertRaises(GroupUnavailable):
                group_service.list_groups("user-jwt")

    def test_timeout_does_not_retry_creation(self):
        with patch.object(httpx.Client, "send", side_effect=httpx.ReadTimeout("sensitive")) as send:
            with self.assertRaises(GroupUnavailable) as caught:
                group_service.create_group("user-jwt", name="Viaje")
        self.assertEqual(send.call_count, 1)
        self.assertNotIn("sensitive", str(caught.exception))

    def test_sessions_are_not_shared_between_operations(self):
        self.responses = [(200, []), (200, [])]
        group_service.list_groups("user-jwt")
        group_service.list_groups("user-jwt")
        self.assertEqual(len(self.transports), 2)
        self.assertIsNot(self.transports[0], self.transports[1])
