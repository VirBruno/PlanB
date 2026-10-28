"""Comando y contratos SDK reales sin red; selección/constraints en el test SQL."""
import json
from io import StringIO
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

from apps.notifications import reminders
from apps.users.services import clients


class ReminderCommandTests(SimpleTestCase):
    def test_local_command_uses_real_sdk_with_memory_transport(self):
        requests, transports = [], []

        def handle(request):
            requests.append(request)
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.url.path, '/rest/v1/rpc/generate_plan_reminders')
            self.assertEqual(json.loads(request.content), {})
            self.assertEqual(request.headers['apikey'], settings.SUPABASE_SECRET_KEY)
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, json=6 if len(requests) == 1 else 0)

        def transport():
            client = httpx.Client(transport=httpx.MockTransport(handle))
            transports.append(client)
            return client

        with patch.object(clients, 'http_client', side_effect=transport):
            first, repeated = StringIO(), StringIO()
            call_command('generate_plan_reminders', stdout=first)
            call_command('generate_plan_reminders', stdout=repeated)
        self.assertIn('Recordatorios creados: 6.', first.getvalue())
        self.assertIn('Recordatorios creados: 0.', repeated.getvalue())
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(t.is_closed for t in transports))
        self.assertNotIn(settings.SUPABASE_SECRET_KEY, first.getvalue())

    def test_service_rejects_invalid_counts(self):
        for value in (None, True, -1, '3', [], {'count': 3}):
            with self.subTest(value=value), patch.object(clients, 'http_client', side_effect=lambda:
                    httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=value)))):
                with self.assertRaises(reminders.ReminderUnavailable):
                    reminders.generate_plan_reminders()

    def test_provider_error_exits_command_without_secrets_or_false_success(self):
        output = StringIO()
        with patch.object(clients, 'http_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(403, json={'code': '42501', 'message': 'private sql and secret',
                                                         'details': None, 'hint': None})))):
            with self.assertRaises(CommandError) as error:
                call_command('generate_plan_reminders', stdout=output)
        self.assertIn('Es seguro volver a ejecutar', str(error.exception))
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('creados', output.getvalue())

    def test_timeout_does_not_retry_and_closes_transport(self):
        requests, transports = [], []

        def handle(request):
            requests.append(request)
            raise httpx.ReadTimeout('secret')

        def transport():
            client = httpx.Client(transport=httpx.MockTransport(handle))
            transports.append(client)
            return client

        with patch.object(clients, 'http_client', side_effect=transport):
            with self.assertRaises(reminders.ReminderUnavailable):
                reminders.generate_plan_reminders()
        self.assertEqual(len(requests), 1)
        self.assertTrue(transports[0].is_closed)

    def test_generator_accepts_no_user_plan_or_time_arguments(self):
        for field in ('user_id', 'plan_id', 'access_token', 'now'):
            with self.subTest(field=field), self.assertRaises(TypeError):
                reminders.generate_plan_reminders(**{field: 'forged'})

    def test_zero_is_a_successful_run(self):
        with patch.object(reminders, 'generate_plan_reminders', return_value=0) as generate:
            output = StringIO()
            call_command('generate_plan_reminders', stdout=output)
        generate.assert_called_once_with()
        self.assertIn('Recordatorios creados: 0.', output.getvalue())
