"""Bandeja compartida: SDK, JWT, hora local, CSRF y enlaces del recordatorio."""
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.groups.services.exceptions import GroupNotFound
from apps.groups.services.invitation_service import InvitationUnavailable
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.notifications import services
from apps.notifications.tests.test_services import notification, NOTIFICATION_ID
from apps.users.services import clients

PLAN_ID = '080cbcee-8613-422d-83d1-c62c4be01852'


def reminder(**values):
    return {'id': NOTIFICATION_ID, 'type': 'plan_reminder',
            'created_at': '2026-10-26T12:00:00Z', 'read_at': None,
            'plan_id': PLAN_ID, 'plan_name': 'Mates a la plaza',
            'scheduled_at': '2026-10-27T19:22:00Z', 'plan_available': True,
            'group_id': GROUP_ID, 'group_name': 'Futboleros', **values}


class ReminderInboxTests(TestCase):
    def setUp(self):
        self.rows = [reminder()]
        self.requests, self.transports = [], []
        self.unread = 1
        self.now = patch.object(timezone, 'now', return_value=parse_datetime('2026-10-26T23:30:00Z'))
        self.now.start()
        self.addCleanup(self.now.stop)
        for name, value in [('current_user', {'id': USER_ID, 'username': 'Ana'}),
                            ('get_access_token', 'private-jwt')]:
            started = patch(f'apps.users.services.session_service.{name}', return_value=value)
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
        self.assertEqual(request.headers['authorization'], 'Bearer private-jwt')
        path = request.url.path.rsplit('/', 1)[-1]
        if path == 'notifications':
            self.assertEqual(request.method, 'HEAD')
            self.assertEqual(request.url.params['read_at'], 'is.null')
            return httpx.Response(200, headers={'content-range': f'*/{self.unread}'})
        if path == 'list_notifications':
            self.assertEqual(request.method, 'GET')
            return httpx.Response(200, json=self.rows)
        if path == 'mark_notification_read':
            self.assertEqual(request.method, 'POST')
            self.assertEqual(json.loads(request.content), {'p_notification_id': NOTIFICATION_ID})
            self.rows[0]['read_at'] = '2026-10-26T23:30:00Z'
            self.unread -= 1
            return httpx.Response(200, json=NOTIFICATION_ID)
        raise AssertionError(f'HTTP inesperado: {request.method} {request.url.path}')

    def tearDown(self):
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_reminder_appears_with_local_time_and_plan_link(self):
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Mañana · Mates a la plaza')
        self.assertContains(response, '16:22')
        self.assertContains(response, '2026-10-27T16:22:00-03:00')
        self.assertContains(response, f'href="/planes/{PLAN_ID}/"')
        self.assertContains(response, 'Ver plan')
        self.assertContains(response, 'Notificaciones: 1 sin leer')
        self.assertIn('no-store', response['Cache-Control'])

    def test_read_post_with_csrf_updates_inbox_and_badge(self):
        browser = Client(enforce_csrf_checks=True)
        browser.get('/notificaciones/')
        response = browser.post(f'/notificaciones/{NOTIFICATION_ID}/leida/', {
            'csrfmiddlewaretoken': browser.cookies['csrftoken'].value,
            'user_id': 'forged', 'plan_id': 'forged',
        }, follow=True)
        self.assertContains(response, 'Mates a la plaza')
        self.assertNotContains(response, 'Marcar como leída')
        self.assertNotContains(response, 'class="pb-notification-dot"')

    def test_mark_read_requires_post_and_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        url = f'/notificaciones/{NOTIFICATION_ID}/leida/'
        self.assertEqual(browser.get(url).status_code, 405)
        self.assertEqual(browser.post(url).status_code, 403)
        self.assertFalse(self.requests)

    def test_page_reads_never_generate_reminders_or_mark_read(self):
        with patch('apps.notifications.reminders.generate_plan_reminders') as generate:
            self.client.get('/notificaciones/')
        generate.assert_not_called()
        self.assertTrue(all(request.method in ('GET', 'HEAD') for request in self.requests))
        self.assertIsNone(self.rows[0]['read_at'])

    def test_invitations_and_reminders_share_inbox(self):
        self.rows.append(notification(id='25aeec23-9615-4b2d-9362-dfe6bb77fbda'))
        self.unread = 2
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Mates a la plaza')
        self.assertContains(response, 'Aceptar invitación')
        self.assertContains(response, 'Rechazar')
        self.assertContains(response, 'Notificaciones: 2 sin leer')

    def test_no_group_access_keeps_history_without_plan_link(self):
        self.rows = [reminder(plan_available=False, group_id=None, group_name=None)]
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Ya no tenés acceso a este plan.')
        self.assertNotContains(response, f'/planes/{PLAN_ID}/')
        self.assertContains(response, 'Marcar como leída')

    def test_click_target_rechecks_current_membership(self):
        plan = {'id': PLAN_ID, 'group_id': GROUP_ID}
        with patch('apps.plans.views.plan_service.get_plan', return_value=plan) as get_plan, \
             patch('apps.plans.views.group_service.get_group', side_effect=GroupNotFound()) as get_group:
            response = self.client.get(f'/planes/{PLAN_ID}/', {'user_id': 'forged'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(str(get_plan.call_args.args[1]), PLAN_ID)
        self.assertEqual(str(get_group.call_args.args[1]), GROUP_ID)
        self.assertEqual(get_plan.call_args.args[0], 'private-jwt')

    def test_same_day_and_old_reminders_do_not_say_tomorrow(self):
        for scheduled, label in [('2026-10-27T01:30:00Z', 'Hoy'),
                                 ('2026-10-25T19:22:00Z', '25/10/2026')]:
            with self.subTest(scheduled=scheduled):
                self.rows = [reminder(scheduled_at=scheduled)]
                response = self.client.get('/notificaciones/')
                self.assertContains(response, f'{label} · Mates a la plaza')
                self.assertNotContains(response, 'Mañana ·')

    def test_active_timezone_is_used_for_day_and_time(self):
        self.rows = [reminder(scheduled_at='2026-10-27T01:30:00Z')]
        with timezone.override(ZoneInfo('Europe/Madrid')):
            item = services.list_notifications('private-jwt')['items'][0]
        self.assertEqual(item['scheduled_at'].hour, 2)
        self.assertEqual(item['day_label'], 'Hoy')

    def test_invalid_reminder_data_fails_safely(self):
        for values in ({'plan_id': 'bad'}, {'scheduled_at': None}, {'scheduled_at': 'bad'},
                       {'scheduled_at': '2026-10-27T16:22:00'}, {'plan_available': 'true'},
                       {'group_id': 'bad'}, {'group_name': None}, {'plan_name': None}):
            with self.subTest(values=values):
                self.rows = [reminder(**values)]
                with self.assertRaises(InvitationUnavailable):
                    services.list_notifications('private-jwt')

    def test_payload_cannot_override_references_and_extra_fields_are_dropped(self):
        self.rows = [reminder(payload={'plan_id': 'forged', 'group_id': 'forged'}, user_id='private')]
        item = services.list_notifications('private-jwt')['items'][0]
        self.assertEqual(item['plan_id'], PLAN_ID)
        self.assertEqual(item['group_id'], GROUP_ID)
        self.assertNotIn('payload', item)
        self.assertNotIn('user_id', item)

    def test_reminder_content_is_escaped_and_secrets_absent(self):
        self.rows = [reminder(plan_name='<script>alert(1)</script>')]
        response = self.client.get('/notificaciones/')
        self.assertContains(response, '&lt;script&gt;')
        for secret in ('<script>alert', 'private-jwt', settings.SUPABASE_SECRET_KEY):
            self.assertNotContains(response, secret)

    def test_unknown_type_keeps_generic_extensible_rendering(self):
        self.rows = [{'id': NOTIFICATION_ID, 'type': 'future_notice',
                      'created_at': '2026-10-26T12:00:00Z', 'read_at': None}]
        self.assertContains(self.client.get('/notificaciones/'), 'Nueva notificación')

    def test_multiple_plans_and_groups_have_independent_links(self):
        other_plan = 'f1358b92-3653-4792-996e-051434617c76'
        self.rows.append(reminder(id='25aeec23-9615-4b2d-9362-dfe6bb77fbda', plan_id=other_plan,
                                  plan_name='Cena', group_id='ae011de8-2f44-49c0-8a7b-4a8fd67cf938',
                                  group_name='Amigos'))
        response = self.client.get('/notificaciones/')
        self.assertContains(response, f'/planes/{PLAN_ID}/')
        self.assertContains(response, f'/planes/{other_plan}/')
        self.assertContains(response, 'Amigos')
