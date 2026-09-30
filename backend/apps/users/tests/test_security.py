import logging
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, Client

from apps.users.logging_filters import SensitiveDataFilter


class LogSafetyTests(SimpleTestCase):
    def test_confirmation_url_is_redacted_in_access_log(self):
        record = logging.LogRecord("django.server", logging.INFO, "", 0,
            '"GET /auth/confirmar-email/?token_hash=very-sensitive-value HTTP/1.1" 302', (), None)
        SensitiveDataFilter().filter(record)
        self.assertNotIn("very-sensitive-value", record.getMessage())

    def test_credentials_redacted_without_mutating_original_arguments(self):
        record = logging.LogRecord("planb", logging.INFO, "", 0,
            "Authorization: Bearer abc.def.secret refresh_token=refresh-secret password=123456789012", (), None)
        SensitiveDataFilter().filter(record)
        for secret in ("abc.def.secret", "refresh-secret", "123456789012"):
            self.assertNotIn(secret, record.getMessage())

    def test_http_transport_loggers_have_no_output(self):
        for name in ("httpx", "httpcore", "supabase_auth"):
            logger = logging.getLogger(name)
            self.assertFalse(logger.propagate)
            self.assertTrue(all(isinstance(handler, logging.NullHandler) for handler in logger.handlers))


class PublicSecurityTests(TestCase):
    def test_secret_key_never_appears_in_public_pages(self):
        for path in ("/login/", "/registro/", "/auth/confirmar-email/"):
            response = self.client.get(path)
            self.assertNotContains(response, settings.SUPABASE_SECRET_KEY)
            self.assertNotContains(response, settings.SUPABASE_PUBLISHABLE_KEY)

    def test_logout_requires_csrf_and_post(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/logout/").status_code, 405)
        self.assertEqual(client.post("/logout/").status_code, 403)

    def test_registration_and_login_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        for path in ("/login/", "/registro/"):
            self.assertEqual(client.post(path, {}).status_code, 403)

    def test_protected_route_redirects_anonymous_with_safe_next(self):
        response = self.client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/login/?next="))
        self.assertNotIn("supabase", response.url)
