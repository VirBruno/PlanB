"""Confirmación: RPC única con JWT y bandeja compartida con SDK real sin red."""
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from apps.groups.services.exceptions import GroupNotFound
from apps.groups.services.invitation_service import InvitationUnavailable
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.notifications import services
from apps.notifications.tests.test_reminder_inbox import PLAN_ID, reminder
from apps.notifications.tests.test_services import notification, NOTIFICATION_ID
from apps.plans.services import plan_confirmation_service
from apps.plans.services.exceptions import InvalidPlan, PlanUnavailable
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired


def confirmed(**values):
    return reminder(**({'type': 'plan_confirmed', 'scheduled_at': '2026-11-07T19:22:00Z'} | values))


class ConfirmationRPCContractTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.transports = [], []

    def transport(self, handler):
        def handle(request):
            self.requests.append(request)
            self.assertEqual(request.headers['apikey'], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers['authorization'], 'Bearer confirmation-jwt')
            return handler(request)
        transport = httpx.Client(transport=httpx.MockTransport(handle))
        self.transports.append(transport)
        return transport

    def tearDown(self):
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_confirmation_is_one_rpc_without_separate_notification_write(self):
        with patch.object(clients, 'http_client', side_effect=lambda:
                          self.transport(lambda request: httpx.Response(200, json=11))):
            result = plan_confirmation_service.confirm('confirmation-jwt', plan_id=PLAN_ID, proposal_id=11)
        self.assertEqual(result, 11)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.url.path, '/rest/v1/rpc/confirm_plan_proposal')
        self.assertEqual(json.loads(request.content), {'p_plan_id': PLAN_ID, 'p_proposal_id': 11})

    def test_retry_keeps_existing_already_confirmed_rejection(self):
        def handle(request):
            if len(self.requests) == 1:
                return httpx.Response(200, json=11)
            return httpx.Response(400, json={'code': '22023', 'message': 'Plan is already confirmed',
                                             'details': None, 'hint': None})
        with patch.object(clients, 'http_client', side_effect=lambda: self.transport(handle)):
            self.assertEqual(plan_confirmation_service.confirm('confirmation-jwt', plan_id=PLAN_ID, proposal_id=11), 11)
            with self.assertRaises(InvalidPlan):
                plan_confirmation_service.confirm('confirmation-jwt', plan_id=PLAN_ID, proposal_id=11)
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(r.url.path.endswith('/confirm_plan_proposal') for r in self.requests))

    def test_database_failure_does_not_fall_back_to_separate_notifications(self):
        with patch.object(clients, 'http_client', side_effect=lambda: self.transport(lambda request:
                httpx.Response(500, json={'code': 'PT500', 'message': 'private SQL', 'details': None, 'hint': None}))):
            with self.assertRaises(PlanUnavailable) as error:
                plan_confirmation_service.confirm('confirmation-jwt', plan_id=PLAN_ID, proposal_id=11)
        self.assertNotIn('private SQL', str(error.exception))
        self.assertEqual(len(self.requests), 1)

    def test_no_session_cannot_confirm(self):
        with patch.object(clients, 'http_client') as transport:
            with self.assertRaises(SessionExpired):
                plan_confirmation_service.confirm('', plan_id=PLAN_ID, proposal_id=11)
        transport.assert_not_called()


class PlanConfirmedInboxTests(TestCase):
    def setUp(self):
        self.rows = [confirmed()]
        self.requests, self.transports = [], []
        self.actor = {'id': USER_ID, 'username': 'Ana'}
        for name, kwargs in (
            ('current_user', {'side_effect': lambda request: self.actor}),
            ('get_access_token', {'return_value': 'confirmation-jwt'}),
        ):
            started = patch(f'apps.users.services.session_service.{name}', **kwargs)
            started.start()
            self.addCleanup(started.stop)
        started = patch.object(clients, 'http_client', side_effect=self.transport)
        started.start()
        self.addCleanup(started.stop)

    def transport(self):
        transport = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.transports.append(transport)
        return transport

    def handle(self, request):
        self.requests.append(request)
        self.assertEqual(request.headers['apikey'], settings.SUPABASE_PUBLISHABLE_KEY)
        self.assertEqual(request.headers['authorization'], 'Bearer confirmation-jwt')
        path = request.url.path.rsplit('/', 1)[-1]
        if path == 'notifications':
            self.assertEqual(request.method, 'HEAD')
            self.assertEqual(request.url.params['read_at'], 'is.null')
            count = sum(row['read_at'] is None for row in self.rows)
            return httpx.Response(200, headers={'content-range': f'*/{count}'})
        if path == 'list_notifications':
            self.assertEqual(request.method, 'GET')
            page = int(request.url.params['p_page'])
            offset = (page - 1) * 12
            return httpx.Response(200, json=self.rows[offset:offset + 13])
        if path == 'mark_notification_read':
            self.assertEqual(request.method, 'POST')
            self.assertEqual(json.loads(request.content), {'p_notification_id': NOTIFICATION_ID})
            self.rows[0]['read_at'] = '2026-10-26T23:30:00Z'
            return httpx.Response(200, json=NOTIFICATION_ID)
        raise AssertionError(f'HTTP inesperado: {request.method} {request.url.path}')

    def tearDown(self):
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_confirmation_shows_local_date_time_badge_and_correct_link(self):
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Plan confirmado · Mates a la plaza')
        self.assertContains(response, 'El encuentro quedó confirmado para el')
        self.assertContains(response, '7 de noviembre de 2026 a las 16:22')
        self.assertContains(response, '2026-11-07T16:22:00-03:00')
        self.assertContains(response, f'href="/planes/{PLAN_ID}/"')
        self.assertContains(response, 'Notificaciones: 1 sin leer')
        self.assertIn('no-store', response['Cache-Control'])

    def test_all_three_types_share_inbox_and_badge(self):
        self.rows += [reminder(id='25aeec23-9615-4b2d-9362-dfe6bb77fbda'),
                      notification(id='41fb6e94-a5ba-440c-862f-445ab2aa2b13')]
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Plan confirmado · Mates a la plaza')
        self.assertContains(response, 'RECORDATORIO')
        self.assertContains(response, 'Aceptar invitación')
        self.assertContains(response, 'Notificaciones: 3 sin leer')
        self.assertContains(response, f'href="/planes/{PLAN_ID}/"', count=2)

    def test_mark_as_read_with_csrf_keeps_reminder_unread(self):
        self.rows.append(reminder(id='25aeec23-9615-4b2d-9362-dfe6bb77fbda'))
        browser = Client(enforce_csrf_checks=True)
        browser.get('/notificaciones/')
        response = browser.post(f'/notificaciones/{NOTIFICATION_ID}/leida/', {
            'csrfmiddlewaretoken': browser.cookies['csrftoken'].value,
            'plan_id': 'forged', 'user_id': 'forged',
        }, follow=True)
        self.assertContains(response, 'Notificaciones: 1 sin leer')
        self.assertIsNotNone(self.rows[0]['read_at'])
        self.assertIsNone(self.rows[1]['read_at'])
        self.assertContains(response, 'Marcar como leída', count=1)

    def test_former_member_sees_history_without_access_link(self):
        self.rows = [confirmed(plan_available=False, group_id=None, group_name=None)]
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Ya no tenés acceso a este plan.')
        self.assertContains(response, 'Plan confirmado ·')
        self.assertNotContains(response, f'/planes/{PLAN_ID}/')

    def test_stale_link_rechecks_current_group_membership(self):
        with patch('apps.plans.views.plan_service.get_plan', return_value={'id': PLAN_ID, 'group_id': GROUP_ID}), \
             patch('apps.plans.views.group_service.get_group', side_effect=GroupNotFound()) as get_group:
            response = self.client.get(f'/planes/{PLAN_ID}/')
        self.assertEqual(response.status_code, 404)
        get_group.assert_called_once_with('confirmation-jwt', GROUP_ID)

    def test_midnight_conversion_changes_local_date_and_honors_active_zone(self):
        self.rows[0]['scheduled_at'] = '2026-11-08T01:22:00Z'
        response = self.client.get('/notificaciones/')
        self.assertContains(response, '7 de noviembre de 2026 a las 22:22')
        with timezone.override(ZoneInfo('Europe/Madrid')):
            response = self.client.get('/notificaciones/')
        self.assertContains(response, '8 de noviembre de 2026 a las 02:22')

    def test_invalid_confirmation_data_is_rejected(self):
        for values in ({'plan_id': 'bad'}, {'plan_available': 'true'}, {'plan_name': None},
                       {'scheduled_at': '2026-11-07T16:22:00'}, {'scheduled_at': None}):
            with self.subTest(values=values):
                self.rows = [confirmed() | values]
                with self.assertRaises(InvitationUnavailable):
                    services.list_notifications('confirmation-jwt')

    def test_payload_does_not_override_authorized_plan_reference(self):
        self.rows = [confirmed(payload={'plan_id': 'forged', 'plan_available': True})]
        item = services.list_notifications('confirmation-jwt')['items'][0]
        self.assertEqual(item['plan_id'], PLAN_ID)
        self.assertNotIn('payload', item)

    def test_confirmation_content_is_escaped(self):
        self.rows = [confirmed(plan_name='<script>alert(1)</script>')]
        response = self.client.get('/notificaciones/')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_pagination_still_uses_twelve_items_and_extra_row(self):
        self.rows = [confirmed(id=f'00000000-0000-4000-8000-{i:012d}') for i in range(13)]
        first = self.client.get('/notificaciones/')
        self.assertContains(first, 'Plan confirmado ·', count=12)
        self.assertContains(first, '?page=2')
        second = self.client.get('/notificaciones/?page=2')
        self.assertContains(second, 'Plan confirmado ·', count=1)
        self.assertContains(second, '?page=1')

    def test_render_never_confirms_or_writes_notifications(self):
        with patch.object(plan_confirmation_service, 'confirm') as confirm:
            self.client.get('/notificaciones/')
        confirm.assert_not_called()
        self.assertTrue(all(r.method in ('GET', 'HEAD') for r in self.requests))

    def test_anonymous_cannot_read_confirmations(self):
        self.actor = None
        response = self.client.get('/notificaciones/')
        self.assertTrue(response.url.startswith('/login/?next='))
        self.assertFalse(self.requests)
