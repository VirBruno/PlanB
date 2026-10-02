import time
from unittest.mock import patch, sentinel

from django.test import Client, TestCase
from django.urls import reverse

from apps.users.services.exceptions import (
    ConfirmationInvalid,
    InvalidCredentials,
    RegistrationRejected,
    ServiceUnavailable,
    UsernameUnavailable,
)
from apps.users.views import PENDING_CONFIRMATION


class RegistrationTests(TestCase):
    def setUp(self):
        self.url = reverse("users:register")
        self.data = {
            "username": "Facundo", "email": "TEST@EXAMPLE.COM",
            "password": "RegistroPass12", "password_confirm": "RegistroPass12",
        }

    def test_get_renders_form_with_privacy_headers_and_accessible_labels(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'for="id_username"')
        self.assertContains(response, 'autocomplete="new-password"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    @patch("apps.users.views.auth_service.sign_up")
    def test_invalid_data_never_calls_provider(self, sign_up):
        response = self.client.post(self.url, {**self.data, "password_confirm": "different"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Las contraseñas no coinciden.")
        sign_up.assert_not_called()
        self.assertNotContains(response, self.data["password"])

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_up", return_value=sentinel.session)
    def test_immediate_session_registration_redirects_to_dashboard(self, sign_up, establish):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse("users:dashboard"), fetch_redirect_response=False)
        sign_up.assert_called_once_with(username="Facundo", email="test@example.com", password="RegistroPass12")
        self.assertEqual(establish.call_args.args[1], sentinel.session)

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_up", return_value=None)
    def test_confirmation_required_does_not_authenticate(self, sign_up, establish):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse("users:email_confirmation"))
        establish.assert_not_called()
        self.assertFalse(self.client.session.get("planb_authenticated"))

    @patch("apps.users.views.auth_service.sign_up", side_effect=UsernameUnavailable())
    def test_username_unavailable_has_field_error(self, sign_up):
        response = self.client.post(self.url, self.data)
        self.assertContains(response, "Ese nombre de usuario ya está en uso.")
        self.assertIn("username", response.context["form"].errors)

    @patch("apps.users.views.auth_service.sign_up", side_effect=RegistrationRejected())
    def test_rejected_registration_explains_without_disclosing_details(self, sign_up):
        response = self.client.post(self.url, self.data)
        self.assertContains(response, "No pudimos completar el registro.")
        self.assertNotContains(response, "RegistrationRejected")

    @patch("apps.users.views.auth_service.sign_up", side_effect=ServiceUnavailable())
    def test_provider_unavailable_is_safe_503(self, sign_up):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "No pudimos conectar", status_code=503)
        self.assertNotContains(response, self.data["password"], status_code=503)


class LoginTests(TestCase):
    def setUp(self):
        self.url = reverse("users:login")
        self.data = {"identifier": " PERSONA@EXAMPLE.COM ", "password": "  exactPass1  "}

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_in", return_value=sentinel.session)
    def test_login_passes_normalized_identifier_and_exact_password(self, sign_in, establish):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse("users:dashboard"), fetch_redirect_response=False)
        sign_in.assert_called_once_with(identifier="persona@example.com", password="  exactPass1  ")
        self.assertEqual(establish.call_args.args[1], sentinel.session)

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_in", return_value=sentinel.session)
    def test_username_login(self, sign_in, establish):
        self.client.post(self.url, {**self.data, "identifier": "FaCuNDo"})
        sign_in.assert_called_once_with(identifier="facundo", password=self.data["password"])

    @patch("apps.users.views.auth_service.sign_in", side_effect=InvalidCredentials())
    def test_credentials_error_generic_and_password_not_rendered(self, sign_in):
        response = self.client.post(self.url, self.data)
        self.assertContains(response, "Usuario/email o contraseña incorrectos.")
        self.assertNotContains(response, self.data["password"])

    @patch("apps.users.views.auth_service.sign_in", side_effect=ServiceUnavailable())
    def test_network_error_is_not_credentials_error(self, sign_in):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "Usuario/email o contraseña incorrectos.", status_code=503)

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_in", return_value=sentinel.session)
    def test_unsafe_next_values_fall_back(self, sign_in, establish):
        for target in ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/%2fevil.example", "/%5cevil.example", "/%0devil.example", "https://testserver/dashboard/"]:
            with self.subTest(target=target):
                response = self.client.post(self.url, {**self.data, "next": target})
                self.assertRedirects(response, reverse("users:dashboard"), fetch_redirect_response=False)

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.sign_in", return_value=sentinel.session)
    def test_internal_next_preserved(self, sign_in, establish):
        target = "/dashboard/?tab=grupos"
        response = self.client.post(self.url, {**self.data, "next": target})
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_get_does_not_render_external_next(self):
        response = self.client.get(self.url, {"next": "https://evil.example"})
        self.assertNotContains(response, "evil.example")
        self.assertContains(response, 'name="next" value="/dashboard/"')


class EmailConfirmationTests(TestCase):
    token = "a1234567890bcdef" * 4

    def setUp(self):
        self.url = reverse("users:email_confirmation")

    def add_pending(self, expires_at=None):
        session = self.client.session
        session[PENDING_CONFIRMATION] = {
            "token_hash": self.token,
            "expires_at": time.time() + 600 if expires_at is None else expires_at,
        }
        session.save()

    @patch("apps.users.views.auth_service.verify_email")
    def test_get_stores_hash_server_side_then_redirects_to_clean_url(self, verify):
        response = self.client.get(self.url, {"token_hash": self.token, "email": "discard@example.com", "type": "recovery"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.url)
        pending = self.client.session[PENDING_CONFIRMATION]
        self.assertEqual(set(pending), {"token_hash", "expires_at"})
        self.assertEqual(pending["token_hash"], self.token)
        self.assertAlmostEqual(pending["expires_at"], time.time() + 600, delta=5)
        verify.assert_not_called()
        clean_response = self.client.get(response["Location"])
        self.assertContains(clean_response, "Confirmar mi email")
        self.assertNotContains(clean_response, self.token)
        self.assertNotContains(clean_response, "discard@example.com")
        self.assertEqual(clean_response["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", clean_response["Cache-Control"])

    @patch("apps.users.views.auth_service.verify_email")
    def test_invalid_hash_is_discarded_and_url_cleaned(self, verify):
        for value in ["bad", "<script>" * 5, "a" * 257, ""]:
            with self.subTest(value=value):
                self.add_pending()
                response = self.client.get(self.url, {"token_hash": value})
                self.assertEqual(response["Location"], self.url)
                self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        verify.assert_not_called()

    @patch("apps.users.views.session_service.establish_session")
    @patch("apps.users.views.auth_service.verify_email")
    def test_post_consumes_before_verification_and_does_not_login(self, verify, establish):
        self.add_pending()
        def check_consumed(token):
            self.assertEqual(token, self.token)
            self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        verify.side_effect = check_consumed
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("users:login"))
        verify.assert_called_once_with(self.token)
        establish.assert_not_called()
        self.assertFalse(self.client.session.get("planb_authenticated"))

    @patch("apps.users.views.auth_service.verify_email")
    def test_repeated_post_does_not_verify_twice(self, verify):
        self.add_pending()
        self.client.post(self.url)
        self.client.post(self.url)
        verify.assert_called_once()

    @patch("apps.users.views.auth_service.verify_email")
    def test_expired_or_missing_pending_cannot_verify(self, verify):
        self.client.post(self.url)
        self.add_pending(expires_at=time.time() - 10)
        response = self.client.post(self.url, follow=True)
        verify.assert_not_called()
        self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        self.assertContains(response, "El enlace venció o ya fue utilizado.")

    @patch("apps.users.views.auth_service.verify_email", side_effect=ConfirmationInvalid())
    def test_failed_verification_discards_pending(self, verify):
        self.add_pending()
        response = self.client.post(self.url, follow=True)
        self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        self.assertContains(response, "El enlace venció o ya fue utilizado.")
        self.assertNotContains(response, self.token)

    @patch("apps.users.views.auth_service.verify_email", side_effect=ServiceUnavailable())
    def test_network_error_also_discards_pending(self, verify):
        self.add_pending()
        response = self.client.post(self.url, follow=True)
        self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        self.assertContains(response, "No pudimos verificar el email.")

    def test_get_after_expiry_cleans_pending(self):
        self.add_pending(expires_at=time.time() - 1)
        response = self.client.get(self.url)
        self.assertNotIn(PENDING_CONFIRMATION, self.client.session)
        self.assertNotContains(response, "Confirmar mi email")


class ProtectedRoutesTests(TestCase):
    def setUp(self):
        for target, value in [
            ("apps.users.views.group_service.list_groups", {"items": [], "page": 1}),
            ("apps.users.services.session_service.get_access_token", "test-jwt"),
        ]:
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    @patch("apps.users.services.session_service.current_user", return_value=None)
    def test_dashboard_requires_session_and_preserves_path(self, current):
        response = self.client.get(reverse("users:dashboard"))
        self.assertRedirects(response, "/login/?next=%2Fdashboard%2F")

    @patch("apps.users.services.session_service.current_user", return_value={"id": "example-id", "username": "Facundo"})
    def test_dashboard_greets_user_and_shows_empty_groups(self, current):
        response = self.client.get(reverse("users:dashboard"))
        self.assertContains(response, "Facundo")
        self.assertContains(response, 'class="brand" href="/dashboard/"')
        self.assertContains(response, "Todavía no participás de ningún grupo.")
        self.assertContains(response, 'method="post" action="/logout/"')
        self.assertIn("no-store", response["Cache-Control"])

    @patch("apps.users.services.session_service.current_user", return_value={"id": "example-id", "username": "<script>alert(1)</script>"})
    def test_profile_text_is_escaped(self, current):
        response = self.client.get(reverse("users:dashboard"))
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    @patch("apps.users.services.session_service.current_user", side_effect=ServiceUnavailable())
    def test_profile_service_outage_uses_503_layout(self, current):
        response = self.client.get(reverse("users:dashboard"))
        self.assertContains(response, "Necesitamos", status_code=503)
        self.assertContains(response, "Volver a intentar", status_code=503)

    @patch("apps.users.views.session_service.logout")
    def test_logout_requires_post(self, logout):
        response = self.client.get(reverse("users:logout"))
        self.assertEqual(response.status_code, 405)
        logout.assert_not_called()

    @patch("apps.users.views.session_service.logout")
    def test_logout_redirects_to_login(self, logout):
        response = self.client.post(reverse("users:logout"))
        self.assertRedirects(response, reverse("users:login"))
        logout.assert_called_once()


class CsrfTests(TestCase):
    def test_post_forms_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        for name in ["register", "login", "email_confirmation", "logout"]:
            with self.subTest(name=name):
                response = client.post(reverse(f"users:{name}"), {"token_hash": "a" * 64})
                self.assertEqual(response.status_code, 403)

    @patch("apps.users.views.auth_service.verify_email")
    def test_confirmation_works_with_browser_csrf_token(self, verify):
        client = Client(enforce_csrf_checks=True)
        url = reverse("users:email_confirmation")
        client.get(url, {"token_hash": "a" * 64}, follow=True)
        token = client.cookies["csrftoken"].value
        response = client.post(url, {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("users:login"))
        verify.assert_called_once_with("a" * 64)
