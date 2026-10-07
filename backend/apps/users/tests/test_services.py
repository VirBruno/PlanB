"""Comportamientos y traducción segura de errores, sin proveedor externo."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from django.test import SimpleTestCase
from supabase_auth.errors import AuthApiError

from apps.users.services import auth_service, profile_service
from apps.users.services.exceptions import (
    ConfirmationInvalid,
    InvalidCredentials,
    RegistrationRejected,
    ServiceUnavailable,
    SessionExpired,
    UsernameUnavailable,
)

USER_ID = "e6e6371c-79bd-4888-8fdd-40ce6c2ba55f"


def auth_response():
    user = SimpleNamespace(id=USER_ID)
    return SimpleNamespace(
        user=user,
        session=SimpleNamespace(
            access_token="test-access-secret",
            refresh_token="test-refresh-secret",
            expires_at=2000000000,
            user=user,
        ),
    )


class AuthServiceTests(SimpleTestCase):
    def setUp(self):
        self.client = MagicMock()
        factory = patch("apps.users.services.clients.public_client")
        self.public = factory.start()
        self.public.return_value.__enter__.return_value = self.client
        self.addCleanup(factory.stop)

    def test_email_login_normalizes_identifier_but_preserves_password(self):
        self.client.auth.sign_in_with_password.return_value = auth_response()
        with patch.object(profile_service, "resolve_email") as resolve:
            session = auth_service.sign_in("  ANA@EXAMPLE.COM ", " password123456 ")
        resolve.assert_not_called()
        self.client.auth.sign_in_with_password.assert_called_once_with({
            "email": "ana@example.com", "password": " password123456 ",
        })
        self.assertEqual(session.user_id, USER_ID)
        self.assertNotIn("test-access-secret", repr(session))
        self.assertNotIn("test-refresh-secret", repr(session))

    def test_username_login_resolves_email_in_backend(self):
        self.client.auth.sign_in_with_password.return_value = auth_response()
        with patch.object(profile_service, "resolve_email", return_value="ana@example.com") as resolve:
            auth_service.sign_in(" ANA ", "password123456")
        resolve.assert_called_once_with("ANA")
        self.client.auth.sign_in_with_password.assert_called_once_with({
            "email": "ana@example.com", "password": "password123456",
        })

    def test_missing_username_and_bad_password_have_same_error(self):
        with patch.object(profile_service, "resolve_email", return_value=None):
            with self.assertRaises(InvalidCredentials) as missing:
                auth_service.sign_in("missing", "password123456")
        self.client.auth.sign_in_with_password.side_effect = AuthApiError(
            "RAW PASSWORD secret", 400, "invalid_credentials",
        )
        with self.assertRaises(InvalidCredentials) as wrong:
            auth_service.sign_in("ana@example.com", "bad")
        self.assertEqual(str(missing.exception), str(wrong.exception))
        self.assertNotIn("secret", str(wrong.exception))

    def test_unconfirmed_email_does_not_enumerate(self):
        self.client.auth.sign_in_with_password.side_effect = AuthApiError("raw", 400, "email_not_confirmed")
        with self.assertRaises(InvalidCredentials):
            auth_service.sign_in("ana@example.com", "password123456")

    def test_temporary_login_errors_allow_retry(self):
        for error in (httpx.ReadTimeout("raw secret"), AuthApiError("raw", 429, "over_request_rate_limit")):
            with self.subTest(error=type(error).__name__):
                self.client.auth.sign_in_with_password.side_effect = error
                with self.assertRaises(ServiceUnavailable):
                    auth_service.sign_in("ana@example.com", "password123456")

    def test_signup_with_immediate_session(self):
        self.client.auth.sign_up.return_value = auth_response()
        with patch.object(profile_service, "username_available", return_value=True):
            session = auth_service.sign_up("Ana", "ANA@EXAMPLE.COM", "password123456")
        self.assertEqual(session.user_id, USER_ID)
        credentials = self.client.auth.sign_up.call_args.args[0]
        self.assertEqual(credentials["options"]["data"], {"username": "Ana"})
        self.assertEqual(credentials["email"], "ana@example.com")

    def test_signup_without_session_never_treats_returned_id_as_authenticated(self):
        self.client.auth.sign_up.return_value = SimpleNamespace(session=None, user=SimpleNamespace(id="obfuscated"))
        with patch.object(profile_service, "username_available", return_value=True):
            self.assertIsNone(auth_service.sign_up("Ana", "ana@example.com", "password123456"))

    def test_existing_email_uses_generic_confirmation_response(self):
        self.client.auth.sign_up.side_effect = AuthApiError("raw", 422, "user_already_exists")
        with patch.object(profile_service, "username_available", return_value=True):
            self.assertIsNone(auth_service.sign_up("Ana", "ana@example.com", "password123456"))

    def test_occupied_username_prevents_signup(self):
        with patch.object(profile_service, "username_available", return_value=False):
            with self.assertRaises(UsernameUnavailable):
                auth_service.sign_up("Ana", "ana@example.com", "password123456")
        self.client.auth.sign_up.assert_not_called()

    def test_duplicate_race_rechecks_after_atomic_trigger_failure(self):
        self.client.auth.sign_up.side_effect = AuthApiError("database raw", 500, "unexpected_failure")
        with patch.object(profile_service, "username_available", side_effect=[True, False]):
            with self.assertRaises(UsernameUnavailable):
                auth_service.sign_up("Ana", "ana@example.com", "password123456")

    def test_registration_failure_is_sanitized(self):
        self.client.auth.sign_up.side_effect = AuthApiError("raw password", 422, "weak_password")
        with patch.object(profile_service, "username_available", return_value=True):
            with self.assertRaises(RegistrationRejected) as caught:
                auth_service.sign_up("Ana", "ana@example.com", "password123456")
        self.assertNotIn("raw password", str(caught.exception))

    def test_refresh_passes_token_explicitly_and_returns_new_pair(self):
        self.client.auth.refresh_session.return_value = auth_response()
        session = auth_service.refresh("old-refresh")
        self.client.auth.refresh_session.assert_called_once_with(refresh_token="old-refresh")
        self.assertEqual(session.refresh_token, "test-refresh-secret")
        self.client.auth.get_session.assert_not_called()
        self.client.auth.set_session.assert_not_called()

    def test_invalid_refresh_expires_local_session(self):
        self.client.auth.refresh_session.side_effect = AuthApiError("raw", 400, "session_not_found")
        with self.assertRaises(SessionExpired):
            auth_service.refresh("invalid-refresh")

    def test_unavailable_refresh_has_distinct_error(self):
        self.client.auth.refresh_session.side_effect = httpx.ConnectTimeout("raw")
        with self.assertRaises(ServiceUnavailable):
            auth_service.refresh("refresh")

    def test_empty_refresh_does_not_restore_sdk_session(self):
        with self.assertRaises(SessionExpired):
            auth_service.refresh("")
        self.client.auth.refresh_session.assert_not_called()

    def test_get_user_verifies_explicit_jwt(self):
        self.client.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id=USER_ID))
        self.assertEqual(auth_service.get_user("token"), USER_ID)
        self.client.auth.get_user.assert_called_once_with(jwt="token")

    def test_invalid_user_jwt_expires_session(self):
        self.client.auth.get_user.side_effect = AuthApiError("raw", 401, "bad_jwt")
        with self.assertRaises(SessionExpired):
            auth_service.get_user("bad-token")

    def test_confirmation_verifies_only_hash_and_type_and_revokes_session(self):
        self.client.auth.verify_otp.return_value = auth_response()
        with patch.object(auth_service, "sign_out") as logout:
            self.assertIsNone(auth_service.verify_email("hash-secret"))
        self.client.auth.verify_otp.assert_called_once_with({"token_hash": "hash-secret", "type": "email"})
        logout.assert_called_once_with("test-access-secret")

    def test_confirmation_still_succeeds_when_cleanup_remote_fails(self):
        self.client.auth.verify_otp.return_value = auth_response()
        with patch.object(auth_service, "sign_out", side_effect=ServiceUnavailable()):
            self.assertIsNone(auth_service.verify_email("hash"))

    def test_confirmation_error_is_safe(self):
        self.client.auth.verify_otp.side_effect = AuthApiError("raw hash-secret", 403, "otp_expired")
        with self.assertRaises(ConfirmationInvalid) as caught:
            auth_service.verify_email("hash-secret")
        self.assertNotIn("hash-secret", str(caught.exception))


class ProfileServiceTests(SimpleTestCase):
    def setUp(self):
        self.client = MagicMock()
        factory = patch("apps.users.services.clients.administrative_client")
        self.administrative = factory.start()
        self.administrative.return_value.__enter__.return_value = self.client
        self.addCleanup(factory.stop)
        self.query = self.client.table.return_value.select.return_value.eq.return_value.limit.return_value

    def test_username_availability_uses_exact_normalized_equality(self):
        self.query.execute.return_value.data = []
        self.assertTrue(profile_service.username_available(" Ana_Test "))
        self.client.table.return_value.select.return_value.eq.assert_called_once_with("username_normalized", "ana_test")

    def test_case_insensitive_occupied_username(self):
        self.query.execute.return_value.data = [{"id": USER_ID}]
        self.assertFalse(profile_service.username_available("ANA"))

    def test_invalid_username_never_queries_provider(self):
        self.assertFalse(profile_service.username_available("invalid@name"))
        self.assertIsNone(profile_service.resolve_email("invalid@name"))
        self.administrative.assert_not_called()

    def test_resolution_requests_one_user_by_uuid_not_user_list(self):
        self.query.execute.return_value.data = [{"id": USER_ID}]
        self.client.auth.admin.get_user_by_id.return_value = SimpleNamespace(user=SimpleNamespace(email="ana@example.com"))
        self.assertEqual(profile_service.resolve_email("ana"), "ana@example.com")
        self.client.auth.admin.get_user_by_id.assert_called_once_with(USER_ID)
        self.client.auth.admin.list_users.assert_not_called()

    def test_missing_username_never_calls_admin_user_endpoint(self):
        self.query.execute.return_value.data = []
        self.assertIsNone(profile_service.resolve_email("missing"))
        self.client.auth.admin.get_user_by_id.assert_not_called()

    def test_profile_uses_user_jwt_and_returns_only_public_fields(self):
        self.query.execute.return_value.data = [{"id": USER_ID, "username": "Ana", "secret": "hidden"}]
        with patch("apps.users.services.clients.public_client") as public:
            public.return_value.__enter__.return_value = self.client
            result = profile_service.get_profile("access", USER_ID)
        public.assert_called_once_with(access_token="access")
        self.administrative.assert_not_called()
        self.assertEqual(result, {"id": USER_ID, "username": "Ana"})

    def test_usernames_for_ids_uses_admin_only_for_requested_profile_ids(self):
        other_id = "a2e0c8f4-e4ae-4ee3-927a-9552974165f6"
        self.client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"id": USER_ID, "username": "Ana"},
            {"id": other_id, "username": "Bruno"},
        ]
        self.assertEqual(profile_service.usernames_for_ids([USER_ID, other_id]), {
            USER_ID: "Ana", other_id: "Bruno",
        })
        self.client.table.assert_called_once_with("profiles")
        self.client.table.return_value.select.return_value.in_.assert_called_once_with(
            "id", sorted([USER_ID, other_id]),
        )

    def test_usernames_for_empty_ids_does_not_query_provider(self):
        self.assertEqual(profile_service.usernames_for_ids([]), {})
        self.administrative.assert_not_called()

    def test_missing_profile_is_service_problem_not_invalid_credentials(self):
        self.query.execute.return_value.data = []
        with patch("apps.users.services.clients.public_client") as public:
            public.return_value.__enter__.return_value = self.client
            with self.assertRaises(ServiceUnavailable):
                profile_service.get_profile("access", USER_ID)
