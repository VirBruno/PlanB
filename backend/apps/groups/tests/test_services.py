import hashlib
from unittest.mock import patch
from django.test import SimpleTestCase
from apps.groups.services import group_service, invitation_service
from apps.users.services.exceptions import SessionExpired


class MissingSessionTests(SimpleTestCase):
    @patch("apps.groups.services.group_service.clients.public_client")
    def test_missing_token_never_opens_client(self, client):
        operations = [
            lambda: group_service.create_group("", name="Grupo"),
            lambda: group_service.list_groups(None),
            lambda: group_service.get_group("", "unused"),
        ]
        for operation in operations:
            with self.assertRaises(SessionExpired):
                operation()
        client.assert_not_called()


class InviteLinkServiceTests(SimpleTestCase):
    @patch("apps.groups.services.invitation_service.rpc")
    @patch("apps.groups.services.invitation_service.secrets.token_urlsafe", return_value="X" * 43)
    def test_creation_sends_only_the_token_hash_to_supabase(self, token_urlsafe, rpc):
        rpc.return_value = "2026-10-25T00:00:00+00:00"
        result = invitation_service.create_invite_link("user-jwt", "7139c649-18c5-4743-b18d-c891c461e80d")
        self.assertEqual(result["token"], "X" * 43)
        self.assertEqual(rpc.call_args.args[1], "create_group_invite_link")
        self.assertEqual(rpc.call_args.args[2]["p_token_hash"], hashlib.sha256(
            ("X" * 43).encode("ascii"),
        ).hexdigest())
        self.assertNotIn(result["token"], str(rpc.call_args.args[2]))

    @patch("apps.groups.services.invitation_service.rpc", return_value=[{
        "group_id": "7139c649-18c5-4743-b18d-c891c461e80d",
        "group_name": "Escapadas", "expires_at": "2026-10-25T00:00:00+00:00",
    }])
    def test_preview_hashes_the_bearer_token_before_rpc(self, rpc):
        token = "Y" * 43
        result = invitation_service.preview_invite_link("user-jwt", token)
        self.assertEqual(result["name"], "Escapadas")
        self.assertEqual(rpc.call_args.args[1], "get_group_from_invite_link")
        self.assertEqual(rpc.call_args.args[2]["p_token_hash"], hashlib.sha256(
            token.encode("ascii"),
        ).hexdigest())

    @patch("apps.groups.services.invitation_service.rpc")
    def test_malformed_link_token_is_rejected_without_rpc(self, rpc):
        with self.assertRaises(invitation_service.InvitationNotFound):
            invitation_service.preview_invite_link("user-jwt", "not-a-token")
        rpc.assert_not_called()
