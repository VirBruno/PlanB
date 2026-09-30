from unittest.mock import patch
from django.test import SimpleTestCase
from apps.groups.services import group_service
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
