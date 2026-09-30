"""Recorrido integrado de views, cookies, ORM y servicio de sesión, sin red."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.utils import timezone

from apps.users.models import SupabaseSession
from apps.users.services.exceptions import ServiceUnavailable
from apps.users.services.types import AuthSession


class AuthenticationFlowTests(TestCase):
    def setUp(self):
        groups = patch("apps.users.views.group_service.list_groups", return_value={"items": [], "page": 1})
        groups.start()
        self.addCleanup(groups.stop)
        self.client = Client(enforce_csrf_checks=True)
        self.tokens = AuthSession(
            access_token="integration-access", refresh_token="integration-refresh",
            expires_at=int((timezone.now() + timedelta(hours=1)).timestamp()),
            user_id="fbc61e7c-cf04-45aa-8d32-e9457c462ed4",
        )

    def csrf(self):
        return self.client.cookies["csrftoken"].value

    @patch("apps.users.services.auth_service.sign_out")
    @patch("apps.users.services.profile_service.get_profile")
    @patch("apps.users.services.auth_service.get_user")
    @patch("apps.users.services.auth_service.sign_in")
    def test_login_dashboard_remote_logout_failure_cleans_database_and_cookie(
        self, sign_in, get_user, get_profile, sign_out
    ):
        sign_in.return_value = self.tokens
        get_user.return_value = self.tokens.user_id
        get_profile.return_value = {"id": self.tokens.user_id, "username": "Facundo"}
        sign_out.side_effect = ServiceUnavailable()
        self.client.get("/login/")
        csrf_before = self.csrf()
        response = self.client.post("/login/", {
            "identifier": "Facundo", "password": "Password12345",
            "csrfmiddlewaretoken": csrf_before,
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(self.csrf(), csrf_before)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.assertTrue(session_cookie["httponly"])
        self.assertEqual(session_cookie["samesite"], "Lax")
        key = session_cookie.value
        self.assertTrue(SupabaseSession.objects.filter(pk=key).exists())
        self.assertNotIn("integration-access", str(Session.objects.get(pk=key).get_decoded()))

        response = self.client.get("/dashboard/")
        self.assertContains(response, "Facundo")
        self.assertNotContains(response, "integration-access")
        self.assertNotContains(response, "integration-refresh")
        with self.assertLogs("planb.sessions", level="WARNING"):
            response = self.client.post("/logout/", {"csrfmiddlewaretoken": self.csrf()})
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertFalse(SupabaseSession.objects.exists())
        self.assertFalse(Session.objects.filter(pk=key).exists())
        self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME].value, "")
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)

    @patch("apps.users.services.auth_service.refresh", side_effect=ServiceUnavailable())
    @patch("apps.users.services.auth_service.sign_in")
    def test_failed_refresh_redirects_to_login_and_drops_cookie(self, sign_in, refresh):
        sign_in.return_value = self.tokens
        self.client.get("/login/")
        self.client.post("/login/", {
            "identifier": "Facundo", "password": "Password12345",
            "csrfmiddlewaretoken": self.csrf(),
        })
        SupabaseSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get("/dashboard/")
        self.assertTrue(response.url.startswith("/login/?next="))
        self.assertFalse(SupabaseSession.objects.exists())
        self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME].value, "")

    @patch("apps.users.services.auth_service.sign_up")
    def test_registration_creates_real_local_session_only_when_provider_returns_one(self, sign_up):
        self.client.get("/registro/")
        data = {"username": "Facundo", "email": "f@example.com",
                "password": "Password12345", "password_confirm": "Password12345",
                "csrfmiddlewaretoken": self.csrf()}
        sign_up.return_value = None
        response = self.client.post("/registro/", data)
        self.assertEqual(response.url, "/auth/confirmar-email/")
        self.assertFalse(SupabaseSession.objects.exists())

        sign_up.return_value = self.tokens
        response = self.client.post("/registro/", data)
        self.assertEqual(response.url, "/dashboard/")
        stored = SupabaseSession.objects.get()
        self.assertEqual(stored.access_token, self.tokens.access_token)
        self.assertEqual(stored.session_id, self.client.cookies[settings.SESSION_COOKIE_NAME].value)
