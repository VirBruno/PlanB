"""Contratos HTTP reales del SDK fijado, reemplazando solamente el transporte."""

import json
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from apps.users.services import auth_service, clients, profile_service
from apps.users.services.exceptions import ServiceUnavailable, SessionExpired

USER_ID = "e6e6371c-79bd-4888-8fdd-40ce6c2ba55f"


def user_payload():
    return {
        "id": USER_ID,
        "aud": "authenticated",
        "role": "authenticated",
        "email": "ana@example.com",
        "app_metadata": {},
        "user_metadata": {},
        "created_at": "2026-01-01T00:00:00Z",
    }


def session_payload():
    return {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "expires_at": 2000000000,
        "user": user_payload(),
    }


class SDKContractTests(SimpleTestCase):
    def setUp(self):
        self.requests = []
        self.transports = []
        self.responses = []

        def handle(request):
            self.requests.append(request)
            if not self.responses:
                raise AssertionError("Llamada HTTP inesperada")
            status, payload = self.responses.pop(0)
            return httpx.Response(status, json=payload)

        def factory():
            transport = httpx.Client(
                transport=httpx.MockTransport(handle),
                timeout=10,
                follow_redirects=False,
            )
            self.transports.append(transport)
            return transport

        self.factory = patch.object(clients, "http_client", side_effect=factory)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def tearDown(self):
        self.assertTrue(all(client.is_closed for client in self.transports))
        self.assertFalse(self.responses, "Hay respuestas preparadas que no se consumieron")
        # Verificar la salida HTTP real de TODOS los contratos, no options.headers.
        for request in self.requests:
            authorization = request.headers.get("authorization", "")
            self.assertNotIn(settings.SUPABASE_PUBLISHABLE_KEY, authorization)
            self.assertNotIn(settings.SUPABASE_SECRET_KEY, authorization)

    def test_login_sends_password_to_auth_with_publishable_key(self):
        self.responses = [(200, session_payload())]
        session = auth_service.sign_in("ANA@EXAMPLE.COM", "password123456")
        request = self.requests[0]
        self.assertEqual(request.url.path, "/auth/v1/token")
        self.assertEqual(request.url.params["grant_type"], "password")
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertEqual(json.loads(request.content)["email"], "ana@example.com")
        self.assertEqual(session.refresh_token, "test-refresh-token")
        self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def test_verify_hash_contract_omits_email_and_revokes_returned_session(self):
        self.responses = [(200, session_payload()), (204, None)]
        auth_service.verify_email("hash-test-secret")
        verify, logout = self.requests
        body = json.loads(verify.content)
        self.assertEqual(verify.url.path, "/auth/v1/verify")
        self.assertEqual(body["token_hash"], "hash-test-secret")
        self.assertEqual(body["type"], "email")
        self.assertNotIn("email", body)
        self.assertNotIn("token", body)
        self.assertEqual(logout.url.path, "/auth/v1/logout")
        self.assertEqual(logout.url.params["scope"], "local")

    def test_registration_metadata_and_confirmation_without_session(self):
        self.responses = [(200, []), (200, user_payload())]
        self.assertIsNone(auth_service.sign_up("Ana", "ANA@EXAMPLE.COM", " password123456 "))
        lookup, signup = self.requests
        self.assertEqual(lookup.headers["apikey"], settings.SUPABASE_SECRET_KEY)
        self.assertEqual(signup.url.path, "/auth/v1/signup")
        self.assertEqual(signup.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertEqual(signup.url.params["redirect_to"], settings.DJANGO_PUBLIC_URL + "/auth/confirmar-email/")
        body = json.loads(signup.content)
        self.assertEqual(body["data"], {"username": "Ana"})
        self.assertEqual(body["email"], "ana@example.com")
        self.assertEqual(body["password"], " password123456 ")

    def test_logout_http_uses_user_privileges_only(self):
        self.responses = [(204, None)]
        with self.assertNoLogs("apps.users.services", level="DEBUG"):
            auth_service.sign_out("user-jwt-secret")
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), settings.SUPABASE_URL + "/auth/v1/logout?scope=local")
        self.assertEqual(request.headers["Authorization"], "Bearer user-jwt-secret")
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertEqual(request.content, b"")
        self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def test_logout_rejects_redirects_without_forwarding_credentials(self):
        self.responses = [(302, None)]
        with self.assertRaises(ServiceUnavailable):
            auth_service.sign_out("jwt")
        self.assertEqual(len(self.requests), 1)

    def test_expired_logout_token_is_safe(self):
        self.responses = [(401, {"message": "raw token"})]
        with self.assertRaises(SessionExpired) as caught:
            auth_service.sign_out("token")
        self.assertNotIn("raw token", str(caught.exception))

    def test_remote_logout_failure_is_controlled(self):
        self.responses = [(503, {"message": "sensitive response"})]
        with self.assertRaises(ServiceUnavailable):
            auth_service.sign_out("token")

    def test_refresh_uses_explicit_token_and_publishable_key(self):
        self.responses = [(200, session_payload())]
        auth_service.refresh("old-refresh")
        request = self.requests[0]
        self.assertEqual(request.url.params["grant_type"], "refresh_token")
        self.assertEqual(json.loads(request.content), {"refresh_token": "old-refresh"})
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)

    def test_get_user_sends_explicit_jwt(self):
        self.responses = [(200, user_payload())]
        self.assertEqual(auth_service.get_user("user-jwt"), USER_ID)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/auth/v1/user")
        self.assertEqual(request.headers["Authorization"], "Bearer user-jwt")

    def test_username_resolution_separates_admin_lookup_from_login(self):
        self.responses = [(200, [{"id": USER_ID}]), (200, {"user": user_payload()}), (200, session_payload())]
        auth_service.sign_in("ANA", "password123456")
        lookup, admin, login = self.requests
        self.assertEqual(lookup.url.params["username_normalized"], "eq.ana")
        self.assertEqual(lookup.url.params["select"], "id")
        self.assertEqual(admin.url.path, "/auth/v1/admin/users/" + USER_ID)
        self.assertEqual(lookup.headers["apikey"], settings.SUPABASE_SECRET_KEY)
        self.assertEqual(admin.headers["apikey"], settings.SUPABASE_SECRET_KEY)
        self.assertEqual(login.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(login.headers))
        self.assertEqual(len(self.transports), 2)

    def test_profile_query_uses_user_bearer_and_minimum_columns(self):
        self.responses = [(200, [{"id": USER_ID, "username": "Ana"}])]
        profile_service.get_profile("user-jwt", USER_ID)
        request = self.requests[0]
        self.assertEqual(request.url.params["select"], "id,username")
        self.assertEqual(request.url.params["id"], "eq." + USER_ID)
        self.assertEqual(request.headers["Authorization"], "Bearer user-jwt")
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)

    def test_clients_do_not_share_storage_headers_or_sessions(self):
        self.responses = [(200, session_payload())]
        with clients.public_client() as first:
            first.auth.sign_in_with_password({"email": "ana@example.com", "password": "password123456"})
            self.assertFalse(first.options.persist_session)
            self.assertFalse(first.options.auto_refresh_token)
            self.assertIsNone(first.auth._refresh_token_timer)
            with clients.public_client() as second:
                self.assertIsNot(first.options.storage, second.options.storage)
                self.assertIsNot(first.options.headers, second.options.headers)
                self.assertIsNone(second.auth._in_memory_session)
                self.assertNotIn("test-access-token", str(second.options.headers))
                self.assertIsNone(second.options.storage.get_item(second.auth._storage_key))
            with clients.administrative_client() as administrative:
                self.assertIsNone(administrative.auth._in_memory_session)
                self.assertNotIn("test-access-token", str(administrative.options.headers))
        self.assertEqual(len(self.requests), 1)

    def test_constructor_does_not_read_or_refresh_stored_session(self):
        with patch("supabase_auth.SyncGoTrueClient.get_session", side_effect=AssertionError("restored session")):
            with clients.public_client():
                pass
        self.assertFalse(self.requests)

    def test_public_client_sends_only_publishable_api_key(self):
        self.responses = [(200, session_payload())]
        with clients.public_client() as client:
            client.auth.sign_in_with_password({"email": "ana@example.com", "password": "password123456"})
        request = self.requests[0]
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertNotIn("authorization", request.headers)

    def test_authenticated_client_sends_publishable_key_and_user_jwt(self):
        self.responses = [(200, [])]
        with clients.public_client(access_token="user-access-jwt") as client:
            client.table("profiles").select("id").execute()
        request = self.requests[0]
        self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertEqual(request.headers["authorization"], "Bearer user-access-jwt")

    def test_privileged_client_sends_secret_api_key_without_bearer(self):
        self.responses = [(200, []), (200, {"user": user_payload()})]
        with clients.administrative_client() as client:
            client.table("profiles").select("id").execute()
            client.auth.admin.get_user_by_id(USER_ID)
        for request in self.requests:
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_SECRET_KEY)
            self.assertNotIn("authorization", request.headers)

    def test_same_operation_preserves_session_jwt_then_removes_sdk_signout_fallback(self):
        self.responses = [(200, session_payload()), (200, []), (200, [])]
        with clients.public_client() as client:
            client.auth.sign_in_with_password({"email": "ana@example.com", "password": "password123456"})
            client.table("profiles").select("id").execute()
            # El SDK repone Bearer api-key al recibir SIGNED_OUT: no debe enviarse.
            client._listen_to_auth_events("SIGNED_OUT", None)
            client.table("profiles").select("id").execute()
        login, authenticated, signed_out = self.requests
        self.assertNotIn("authorization", login.headers)
        self.assertEqual(authenticated.headers["authorization"], "Bearer test-access-token")
        self.assertNotIn("authorization", signed_out.headers)
