from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.users.models import SupabaseSession
from apps.users.services import session_service
from apps.users.services.exceptions import InvalidCredentials, ServiceUnavailable, SessionExpired
from apps.users.services.types import AuthSession


class SessionLifecycleTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/dashboard/")
        self.request.session = SessionStore()
        self.request.session["anonymous"] = True
        self.request.session.save()
        self.old_key = self.request.session.session_key
        self.tokens = AuthSession(
            access_token="access-original", refresh_token="refresh-original",
            expires_at=int((timezone.now() + timedelta(hours=1)).timestamp()),
            user_id="a39ce2c2-785a-4f61-b98e-12d5419596e1",
        )

    def establish(self):
        session_service.establish_session(self.request, self.tokens)
        return SupabaseSession.objects.get(session_id=self.request.session.session_key)

    def test_login_rotates_key_and_keeps_tokens_out_of_session_blob(self):
        self.establish()
        self.assertNotEqual(self.old_key, self.request.session.session_key)
        self.assertFalse(Session.objects.filter(pk=self.old_key).exists())
        data = Session.objects.get(pk=self.request.session.session_key).get_decoded()
        self.assertTrue(data["planb_authenticated"])
        self.assertNotIn("access-original", str(data))
        self.assertNotIn("refresh-original", str(data))
        self.assertIn("CSRF_COOKIE", self.request.META)

    @patch("apps.users.services.session_service.profile_service.get_profile")
    @patch("apps.users.services.session_service.auth_service.get_user")
    def test_authenticated_identity_comes_from_supabase(self, get_user, get_profile):
        self.establish()
        get_user.return_value = self.tokens.user_id
        get_profile.return_value = {"id": self.tokens.user_id, "username": "Facundo"}
        self.assertEqual(session_service.current_user(self.request)["username"], "Facundo")
        get_user.assert_called_once_with("access-original")
        get_profile.assert_called_once_with("access-original", self.tokens.user_id)

    @patch("apps.users.services.session_service.auth_service.refresh")
    def test_refresh_updates_pair_and_second_read_uses_new_pair(self, refresh):
        stored = self.establish()
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save()
        refresh.return_value = AuthSession(
            access_token="access-new", refresh_token="refresh-new",
            expires_at=int((timezone.now() + timedelta(hours=1)).timestamp()), user_id=self.tokens.user_id,
        )
        self.assertEqual(session_service._active_token(self.request), "access-new")
        self.assertEqual(session_service._active_token(self.request), "access-new")
        stored.refresh_from_db()
        self.assertEqual(stored.refresh_token, "refresh-new")
        refresh.assert_called_once_with("refresh-original")

    @patch("apps.users.services.session_service.auth_service.refresh")
    def test_failed_refresh_clears_both_rows_even_service_outage(self, refresh):
        for error in (SessionExpired(), ServiceUnavailable()):
            with self.subTest(error=type(error).__name__):
                stored = self.establish()
                stored.expires_at = timezone.now()
                stored.save()
                key = self.request.session.session_key
                refresh.side_effect = error
                self.assertIsNone(session_service._active_token(self.request))
                self.assertFalse(SupabaseSession.objects.filter(pk=key).exists())
                self.assertFalse(Session.objects.filter(pk=key).exists())

    def test_stale_session_cannot_recreate_deleted_tokens(self):
        stored = self.establish()
        key = self.request.session.session_key
        stored.delete()
        self.assertIsNone(session_service._active_token(self.request))
        self.assertFalse(Session.objects.filter(pk=key).exists())
        self.assertEqual(SupabaseSession.objects.count(), 0)

    def test_no_session_is_anonymous(self):
        self.assertIsNone(session_service.current_user(self.request))

    @patch("apps.users.services.session_service.auth_service.sign_out")
    def test_logout_remote_failure_still_deletes_everything(self, sign_out):
        self.establish()
        key = self.request.session.session_key
        sign_out.side_effect = ServiceUnavailable()
        with self.assertLogs("planb.sessions", level="WARNING") as captured:
            session_service.logout(self.request)
        self.assertNotIn("access-original", str(captured.output))
        self.assertFalse(Session.objects.filter(pk=key).exists())
        self.assertFalse(SupabaseSession.objects.exists())
        self.assertIsNone(self.request.session.session_key)

    @patch("apps.users.services.session_service.auth_service.sign_out")
    def test_logout_success_and_repeated_logout(self, sign_out):
        self.establish()
        session_service.logout(self.request)
        session_service.logout(self.request)
        sign_out.assert_called_once_with("access-original")
        self.assertFalse(SupabaseSession.objects.exists())

    @patch("apps.users.services.session_service.auth_service.sign_out")
    @patch("apps.users.services.session_service.auth_service.refresh")
    def test_logout_expired_token_uses_explicit_refresh(self, refresh, sign_out):
        stored = self.establish()
        stored.expires_at = timezone.now() - timedelta(seconds=1)
        stored.save()
        refresh.return_value = AuthSession(
            access_token="access-new", refresh_token="refresh-new",
            expires_at=int((timezone.now() + timedelta(hours=1)).timestamp()), user_id=self.tokens.user_id,
        )
        session_service.logout(self.request)
        refresh.assert_called_once_with("refresh-original")
        sign_out.assert_called_once_with("access-new")
        self.assertFalse(SupabaseSession.objects.exists())

    def test_old_django_snapshot_cannot_overwrite_refreshed_tokens(self):
        stored = self.establish()
        key = self.request.session.session_key
        older_request = SessionStore(key)
        older_request.get("planb_authenticated")
        stored.access_token = "access-new"
        stored.refresh_token = "refresh-new"
        stored.save()
        older_request["message"] = "mensaje de otra pestaña"
        older_request.save()
        stored.refresh_from_db()
        self.assertEqual(stored.access_token, "access-new")
        self.assertEqual(stored.refresh_token, "refresh-new")

    def test_expired_parent_session_cascades_token_deletion(self):
        stored = self.establish()
        Session.objects.filter(pk=stored.session_id).update(expire_date=timezone.now()-timedelta(seconds=1))
        SessionStore.clear_expired()
        self.assertFalse(SupabaseSession.objects.exists())
