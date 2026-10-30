"""Fotos visibles: autorización por contexto, SDK real sin red y render compartido."""
import json
from unittest.mock import patch

import httpx
from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase

from apps.groups.services import group_service
from apps.groups.services.invitation_service import InvitationNotFound
from apps.plans.services.exceptions import PlanNotFound
from apps.plans.tests.test_services import USER_ID, GROUP_ID, PLAN_ID
from apps.plans.tests.test_services import plan_row
from apps.users.services import clients, visible_avatars as service

OTHER = "00000000-0000-0000-0000-000000000002"
STRANGER = "00000000-0000-0000-0000-000000000003"
NOTICE = "00000000-0000-0000-0000-000000000010"
INVITATION = "00000000-0000-0000-0000-000000000011"


def item(scope="group", uid=OTHER, context=GROUP_ID, **kwargs):
    return {"scope": scope, "user": uid, "context": context, **kwargs}


class AvatarAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.members_patch = patch.object(service.invitation_service, "list_members", return_value=[
            {"id": USER_ID, "username": "Ana", "role": "member"},
            {"id": OTHER, "username": "Bruno", "role": "member"}])
        self.members = self.members_patch.start()
        self.addCleanup(self.members_patch.stop)
        signer = patch.object(service, "_sign_authorized", side_effect=lambda ids: {uid: "photo:" + uid for uid in ids})
        self.sign = signer.start()
        self.addCleanup(signer.stop)

    def resolve(self, *items):
        return service.resolve("user-jwt", USER_ID, service.descriptors(list(items)))

    def test_self_is_derived_from_session_not_client(self):
        self.assertEqual(self.resolve(item("self", STRANGER)), ["photo:" + USER_ID])
        self.members.assert_not_called()
        self.sign.assert_called_once_with({USER_ID})

    def test_member_photo_authorized_in_group(self):
        self.assertEqual(self.resolve(item()), ["photo:" + OTHER])
        self.members.assert_called_once_with("user-jwt", GROUP_ID)

    def test_arbitrary_user_is_not_signed(self):
        self.assertEqual(self.resolve(item(uid=STRANGER)), [None])
        self.sign.assert_called_once_with(set())

    def test_outsider_cannot_use_a_known_group_id(self):
        self.members.side_effect = InvitationNotFound()
        self.assertEqual(self.resolve(item()), [None])
        self.sign.assert_called_once_with(set())

    def test_group_roster_and_signing_are_batched(self):
        self.assertEqual(len(self.resolve(item(uid=OTHER), item(uid=USER_ID), item(uid=OTHER))), 3)
        self.members.assert_called_once()
        self.sign.assert_called_once_with({USER_ID, OTHER})

    def test_owner_can_see_pending_invitee_photo(self):
        self.members.return_value[0]["role"] = "owner"
        with patch.object(service.invitation_service, "list_pending", return_value=[{"invited_user_id": STRANGER}]):
            self.assertEqual(self.resolve(item(uid=STRANGER)), ["photo:" + STRANGER])

    def test_member_does_not_query_owner_pending_invitations(self):
        with patch.object(service.invitation_service, "list_pending") as pending:
            self.assertEqual(self.resolve(item(uid=STRANGER)), [None])
        pending.assert_not_called()

    def test_plan_rechecks_access_before_signing(self):
        with patch.object(service.plan_service, "get_plan", side_effect=PlanNotFound()):
            self.assertEqual(self.resolve(item("plan", context=PLAN_ID)), [None])
        self.members.assert_not_called()

    def test_plan_current_members_need_no_historical_queries(self):
        with patch.object(service.plan_service, "get_plan", return_value={"group_id": GROUP_ID}), \
             patch.object(service.proposal_service, "list_proposals") as proposals:
            self.assertEqual(self.resolve(item("plan", context=PLAN_ID)), ["photo:" + OTHER])
        proposals.assert_not_called()

    def test_visible_historical_author_photo_is_allowed(self):
        with patch.object(service.plan_service, "get_plan", return_value={"group_id": GROUP_ID}), \
             patch.object(service.proposal_service, "list_proposals", return_value=[{"created_by": STRANGER}]):
            self.assertEqual(self.resolve(item("plan", STRANGER, PLAN_ID)), ["photo:" + STRANGER])

    def test_visible_historical_expense_person_is_allowed(self):
        with patch.object(service.plan_service, "get_plan", return_value={"group_id": GROUP_ID}), \
             patch.object(service.proposal_service, "list_proposals", return_value=[]), \
             patch.object(service.expense_service, "get_summary", return_value={"people": [{"id": STRANGER}]}):
            self.assertEqual(self.resolve(item("plan", STRANGER, PLAN_ID)), ["photo:" + STRANGER])

    def test_search_must_return_exact_authorized_candidate(self):
        with patch.object(service.invitation_service, "search_invitees", return_value=[{"id": OTHER}]):
            self.assertEqual(self.resolve(item("search", OTHER, GROUP_ID, username="Bruno")), ["photo:" + OTHER])
            self.assertEqual(self.resolve(item("search", STRANGER, GROUP_ID, username="Bruno")), [None])

    def test_invalid_batch_or_scope_is_rejected(self):
        for value in ([item("unknown")], [item(uid="../private")], [item(context="bad")], [item()] * 101, {}):
            with self.subTest(value=str(value)[:50]), self.assertRaises(ValueError):
                service.descriptors(value)


class AvatarSDKTests(SimpleTestCase):
    def setUp(self):
        self.responses, self.requests, self.transports = [], [], []

        def handle(request):
            self.requests.append(request)
            return httpx.Response(200, json=self.responses.pop(0))

        def factory():
            client = httpx.Client(transport=httpx.MockTransport(handle))
            self.transports.append(client)
            return client

        patcher = patch.object(clients, "http_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(c.is_closed for c in self.transports))

    def signed_row(self, uid):
        return {"path": uid + "/avatar", "signedURL": f"/object/sign/profile-avatars/{uid}/avatar?token=fixture", "error": None}

    def test_storage_batch_is_restricted_to_authorized_paths_and_short_ttl(self):
        self.responses = [[self.signed_row(OTHER), self.signed_row(USER_ID)]]
        urls = service._sign_authorized({USER_ID, OTHER})
        self.assertEqual(set(urls), {USER_ID, OTHER})
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/storage/v1/object/sign/profile-avatars")
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_SECRET_KEY)
        self.assertNotEqual(request.headers.get("authorization"), "Bearer user-jwt")
        body = json.loads(request.content)
        self.assertEqual(body["paths"], sorted([USER_ID + "/avatar", OTHER + "/avatar"]))
        self.assertEqual(body["expiresIn"], "300")

    def test_unknown_paths_and_external_urls_are_discarded(self):
        self.responses = [[self.signed_row(STRANGER), self.signed_row(OTHER) | {"signedURL": "https://external.invalid/photo"}]]
        self.assertEqual(service._sign_authorized({OTHER}), {})

    def test_missing_photo_keeps_fallback(self):
        self.responses = [[{"path": OTHER + "/avatar", "signedURL": None, "error": "Object not found"}]]
        self.assertEqual(service._sign_authorized({OTHER}), {})

    def test_notifications_resolve_references_with_user_jwt_in_two_queries(self):
        self.responses = [[{"id": NOTICE, "creditor_id": None, "invitation_id": INVITATION}],
                          [{"id": INVITATION, "invited_by": OTHER}]]
        with patch.object(service, "_sign_authorized", return_value={OTHER: "photo"}) as sign:
            result = service.resolve("user-jwt", USER_ID, service.descriptors([
                item("notification", STRANGER, NOTICE), item("notification", STRANGER, NOTICE)]))
        self.assertEqual(result, ["photo", "photo"])
        sign.assert_called_once_with({OTHER})
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(r.headers["authorization"] == "Bearer user-jwt" for r in self.requests))
        self.assertTrue(all(r.headers["apikey"] == settings.SUPABASE_PUBLISHABLE_KEY for r in self.requests))

    def test_foreign_notification_returns_no_photo_and_never_signs_target(self):
        self.responses = [[]]
        with patch.object(service, "_sign_authorized", return_value={}) as sign:
            result = service.resolve("user-jwt", USER_ID, service.descriptors([item("notification", STRANGER, NOTICE)]))
        self.assertEqual(result, [None])
        sign.assert_called_once_with(set())

    def test_group_names_are_one_jwt_query(self):
        self.responses = [[{"id": GROUP_ID, "name": "Amigos"}]]
        self.assertEqual(group_service.names_for_ids("user-jwt", [GROUP_ID, GROUP_ID]), {GROUP_ID: "Amigos"})
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer user-jwt")


class AvatarViewTests(TestCase):
    def setUp(self):
        for name, value in (("current_user", {"id": USER_ID, "username": "Ana"}), ("get_access_token", "user-jwt")):
            patcher = patch("apps.users.services.session_service." + name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_private_post_response_contains_urls_only(self):
        with patch.object(service, "resolve", return_value=["https://photos.invalid/signed"]) as resolve:
            response = self.client.post("/avatares/", {"avatars": [{"scope": "self"}]}, content_type="application/json")
        self.assertEqual(response.json(), {"urls": ["https://photos.invalid/signed"]})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(resolve.call_args.args[:2], ("user-jwt", USER_ID))

    def test_csrf_and_post_are_required(self):
        self.assertEqual(self.client.get("/avatares/").status_code, 405)
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post("/avatares/", {}, content_type="application/json").status_code, 403)

    def test_unauthenticated_cannot_resolve(self):
        with patch("apps.users.services.session_service.current_user", return_value=None), \
             patch.object(service, "resolve") as resolve:
            self.assertEqual(self.client.post("/avatares/", {}, content_type="application/json").status_code, 302)
        resolve.assert_not_called()

    def test_invalid_json_or_body_is_rejected_without_signing(self):
        with patch.object(service, "resolve") as resolve:
            for data in ("not json", '{"avatars":[] ,"admin":true}', '{"avatars":[{"scope":"all"}]}'):
                self.assertEqual(self.client.post("/avatares/", data, content_type="application/json").status_code, 400)
        resolve.assert_not_called()

    def test_component_is_decorative_and_preserves_fallback(self):
        html = render_to_string("components/user_avatar.html", {"username": "Ana", "scope": "self"})
        self.assertIn('data-avatar-fallback>AN', html)
        self.assertIn('aria-hidden="true"', html)
        self.assertNotIn("user-jwt", html)

    def test_dashboard_art_has_no_group_initials(self):
        html = render_to_string("components/group_art.html", {"variant": 2})
        self.assertIn("<svg", html)
        self.assertIn("#1F4D3A", html)

    def test_plan_cards_show_group_name(self):
        with patch("apps.plans.views.plan_service.list_plans", return_value={"items": [plan_row()], "page": 1}), \
             patch.object(group_service, "names_for_ids", return_value={GROUP_ID: "Amigos"}), \
             patch("apps.notifications.services.unread_count", return_value=0):
            response = self.client.get("/planes/")
        self.assertContains(response, "Grupo: Amigos")
        self.assertContains(response, "data-avatar-endpoint")

    def test_unavailable_group_name_has_explicit_fallback(self):
        with patch("apps.plans.views.plan_service.list_plans", return_value={"items": [plan_row()], "page": 1}), \
             patch.object(group_service, "names_for_ids", return_value={}), \
             patch("apps.notifications.services.unread_count", return_value=0):
            self.assertContains(self.client.get("/planes/"), "Grupo: Grupo no disponible")
