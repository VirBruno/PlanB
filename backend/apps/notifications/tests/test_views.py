from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase
from django.utils.dateparse import parse_datetime

from apps.groups.services.exceptions import GroupNotFound
from apps.groups.services.invitation_service import (
    InvitationConflict, InvitationNotFound, InvitationUnavailable, InvalidInvitation,
)
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.notifications.tests.test_services import INVITATION_ID, NOTIFICATION_ID, notification
from apps.users.services.exceptions import SessionExpired


class InvitationViewsTests(TestCase):
    def mock(self, target, **values):
        started = patch(target, **values)
        mocked = started.start()
        self.addCleanup(started.stop)
        return mocked

    def setUp(self):
        self.current = self.mock('apps.users.services.session_service.current_user',
                                 return_value={'id': USER_ID, 'username': 'Ana'})
        self.mock('apps.users.services.session_service.get_access_token', return_value='private-jwt')
        self.badge = self.mock('apps.notifications.services.unread_count', return_value=2)
        self.group = {'id': GROUP_ID, 'name': 'Futboleros', 'description': '',
                      'role': 'owner', 'role_label': 'Owner'}
        self.get_group = self.mock('apps.groups.views.group_service.get_group', return_value=self.group)
        self.members = self.mock('apps.groups.services.invitation_service.list_members', return_value=[
            {'id': USER_ID, 'username': 'Ana', 'initials': 'AN', 'role': 'owner', 'role_label': 'Owner'},
            {'id': INVITATION_ID, 'username': 'Bruno', 'initials': 'BR', 'role': 'member', 'role_label': 'Miembro'},
        ])
        self.pending = self.mock('apps.groups.services.invitation_service.list_pending', return_value=[])
        self.search = self.mock('apps.groups.services.invitation_service.search_invitees', return_value=[
            {'id': INVITATION_ID, 'username': 'Bruno', 'initials': 'BR'},
        ])
        self.create = self.mock('apps.groups.services.invitation_service.create_invitation', return_value=INVITATION_ID)
        self.accept = self.mock('apps.groups.services.invitation_service.accept_invitation', return_value=GROUP_ID)
        self.reject = self.mock('apps.groups.services.invitation_service.reject_invitation', return_value=INVITATION_ID)
        self.mark_read = self.mock('apps.notifications.services.mark_read', return_value=NOTIFICATION_ID)
        self.item = notification()
        self.item['created_at'] = parse_datetime(self.item['created_at'])
        self.notifications = self.mock('apps.notifications.services.list_notifications', return_value={
            'items': [self.item], 'page': 1,
        })
        self.mock('apps.groups.views.plan_service.list_plans', return_value={'items': [], 'page': 1})
        self.invite_url = f'/grupos/{GROUP_ID}/invitar/'
        self.accept_url = f'/notificaciones/invitaciones/{INVITATION_ID}/aceptar/'
        self.reject_url = f'/notificaciones/invitaciones/{INVITATION_ID}/rechazar/'
        self.read_url = f'/notificaciones/{NOTIFICATION_ID}/leida/'

    def test_owner_can_search_username(self):
        response = self.client.get(self.invite_url, {'username': 'BRUNO'})
        self.assertContains(response, 'Enviar invitación')
        self.search.assert_called_once_with('private-jwt', self.get_group.call_args.args[1], 'BRUNO')
        self.create.assert_not_called()

    def test_invalid_search_does_not_query_profiles(self):
        for username in ('', 'a', '%', 'Bruno@domain.invalid'):
            self.client.get(self.invite_url, {'username': username})
        self.search.assert_not_called()

    def test_pending_candidate_cannot_be_invited_from_ui(self):
        self.pending.return_value = [{'invited_user_id': INVITATION_ID, 'username': 'Bruno', 'initials': 'BR'}]
        response = self.client.get(self.invite_url, {'username': 'Bruno'})
        self.assertContains(response, 'Invitación pendiente')
        self.assertContains(response, 'disabled')
        self.assertNotContains(response, 'Enviar invitación')

    def test_valid_invitation_uses_only_target_and_url_group(self):
        response = self.client.post(self.invite_url, {'invited_user_id': INVITATION_ID,
            'invited_by': 'forged', 'role': 'owner', 'group_id': 'forged', 'user_id': 'forged'})
        self.assertRedirects(response, f'/grupos/{GROUP_ID}/', fetch_redirect_response=False)
        self.assertEqual(self.create.call_args.args[0], 'private-jwt')
        self.assertEqual(str(self.create.call_args.args[1]), GROUP_ID)
        self.assertEqual(str(self.create.call_args.args[2]), INVITATION_ID)
        self.assertFalse(self.create.call_args.kwargs)

    def test_member_cannot_search_or_invite(self):
        self.group['role'] = 'member'
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)(self.invite_url,
                {'username': 'Bruno', 'invited_user_id': INVITATION_ID}).status_code, 403)
        self.search.assert_not_called()
        self.create.assert_not_called()

    def test_outsider_cannot_search_or_invite(self):
        self.get_group.side_effect = GroupNotFound()
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)(self.invite_url).status_code, 404)
        self.search.assert_not_called()
        self.create.assert_not_called()

    def test_invalid_recipient_is_a_form_error(self):
        response = self.client.post(self.invite_url, {'invited_user_id': 'bad'})
        self.assertContains(response, 'data-error-summary')
        self.create.assert_not_called()

    def test_self_or_existing_member_rejection_is_shown(self):
        self.create.side_effect = InvalidInvitation()
        response = self.client.post(self.invite_url, {'invited_user_id': USER_ID})
        self.assertContains(response, 'no podés invitarte', status_code=400)

    def test_duplicate_pending_race_shows_conflict(self):
        self.create.side_effect = InvitationConflict()
        self.assertEqual(self.client.post(self.invite_url, {'invited_user_id': INVITATION_ID}).status_code, 409)

    def test_invite_write_failure_has_no_success_or_retry(self):
        self.create.side_effect = InvitationUnavailable()
        response = self.client.post(self.invite_url, {'invited_user_id': INVITATION_ID})
        self.assertContains(response, 'Revisá el estado', status_code=503)
        self.assertNotContains(response, 'La invitación se envió', status_code=503)
        self.create.assert_called_once()

    def test_invitee_sees_pending_invitation_actions(self):
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Futboleros')
        self.assertContains(response, 'te invitó a unirte al grupo')
        self.assertContains(response, self.accept_url)
        self.assertContains(response, self.reject_url)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    def test_other_user_empty_notifications_has_no_invitation_actions(self):
        self.notifications.return_value = {'items': [], 'page': 1}
        response = self.client.get('/notificaciones/')
        self.assertNotContains(response, self.accept_url)
        self.assertNotContains(response, 'Futboleros')

    def test_accept_ignores_forged_identity_and_group(self):
        response = self.client.post(self.accept_url, {'user_id': 'forged', 'group_id': 'forged', 'role': 'owner'})
        self.assertRedirects(response, '/notificaciones/', fetch_redirect_response=False)
        self.assertEqual(self.accept.call_args.args[0], 'private-jwt')
        self.assertEqual(str(self.accept.call_args.args[1]), INVITATION_ID)
        self.assertFalse(self.accept.call_args.kwargs)

    def test_reject_calls_only_recipient_rpc(self):
        self.assertEqual(self.client.post(self.reject_url).status_code, 302)
        self.reject.assert_called_once()
        self.accept.assert_not_called()

    def test_other_user_cannot_respond(self):
        for operation, url in ((self.accept, self.accept_url), (self.reject, self.reject_url)):
            operation.side_effect = InvitationNotFound()
            self.assertEqual(self.client.post(url).status_code, 404)

    def test_accept_failure_has_no_partial_ui_success(self):
        self.accept.side_effect = InvitationUnavailable()
        response = self.client.post(self.accept_url)
        self.assertContains(response, 'Revisá el estado', status_code=503)
        self.assertNotContains(response, 'Invitación aceptada', status_code=503)
        self.accept.assert_called_once()

    def test_resolved_invitation_is_history_without_response_actions(self):
        for status in ('accepted', 'rejected'):
            self.item['status'] = status
            self.item['read_at'] = self.item['created_at']
            response = self.client.get('/notificaciones/')
            self.assertNotContains(response, self.accept_url)
            self.assertNotContains(response, self.reject_url)
            self.assertContains(response, 'Invitación aceptada' if status == 'accepted' else 'Invitación rechazada')
            if status == 'accepted':
                self.assertContains(response, f'/grupos/{GROUP_ID}/')

    def test_conflict_redirects_to_fresh_state(self):
        self.accept.side_effect = InvitationConflict()
        self.assertRedirects(self.client.post(self.accept_url), '/notificaciones/', fetch_redirect_response=False)

    def test_all_mutations_require_post_and_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        for url in (self.accept_url, self.reject_url, self.read_url):
            self.assertEqual(browser.get(url).status_code, 405)
            self.assertEqual(browser.post(url).status_code, 403)
        self.assertEqual(browser.post(self.invite_url, {'invited_user_id': INVITATION_ID}).status_code, 403)
        self.accept.assert_not_called()
        self.reject.assert_not_called()
        self.mark_read.assert_not_called()
        self.create.assert_not_called()

    def test_browser_csrf_can_accept(self):
        browser = Client(enforce_csrf_checks=True)
        browser.get('/notificaciones/')
        response = browser.post(self.accept_url, {'csrfmiddlewaretoken': browser.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        self.accept.assert_called_once()

    def test_mark_read_does_not_respond_to_invitation(self):
        self.assertEqual(self.client.post(self.read_url).status_code, 302)
        self.mark_read.assert_called_once()
        self.accept.assert_not_called()
        self.reject.assert_not_called()

    def test_other_user_cannot_mark_read(self):
        self.mark_read.side_effect = InvitationNotFound()
        self.assertEqual(self.client.post(self.read_url).status_code, 404)

    def test_badge_counts_only_unread(self):
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Notificaciones: 2 sin leer')
        self.assertContains(response, 'class="notification-badge"')
        self.badge.assert_called_once_with('private-jwt')

    def test_zero_unread_hides_badge(self):
        self.badge.return_value = 0
        self.assertNotContains(self.client.get('/notificaciones/'), 'class="notification-badge"')

    def test_badge_outage_keeps_page_and_reports_unavailable_count(self):
        self.badge.side_effect = InvitationUnavailable()
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'contador no disponible')
        self.assertContains(response, 'Futboleros')

    def test_notification_outage_is_not_an_empty_inbox(self):
        self.notifications.side_effect = InvitationUnavailable()
        response = self.client.get('/notificaciones/')
        self.assertContains(response, 'Volver a intentar', status_code=503)
        self.assertNotContains(response, 'Todavía no tenés', status_code=503)

    def test_members_can_see_members_but_no_admin_actions(self):
        self.group['role'] = 'member'
        response = self.client.get(f'/grupos/{GROUP_ID}/')
        self.assertContains(response, 'Integrantes')
        self.assertContains(response, 'Bruno')
        self.assertContains(response, 'Crear plan')
        for action in ('Invitar integrante', 'Editar grupo', 'Eliminar grupo'):
            self.assertNotContains(response, action)
        self.pending.assert_not_called()

    def test_owner_sees_pending_invites_and_invite_link(self):
        self.pending.return_value = [{'username': 'Carla', 'initials': 'CA'}]
        response = self.client.get(f'/grupos/{GROUP_ID}/')
        self.assertContains(response, 'Invitaciones pendientes')
        self.assertContains(response, 'Carla')
        self.assertContains(response, self.invite_url)

    def test_members_outage_does_not_break_existing_group(self):
        self.members.side_effect = InvitationUnavailable()
        response = self.client.get(f'/grupos/{GROUP_ID}/')
        self.assertContains(response, 'No pudimos cargar los integrantes')
        self.assertContains(response, 'Planes del grupo')

    def test_notification_content_is_escaped_and_secrets_absent(self):
        self.item['group_name'] = '<script>alert(1)</script>'
        self.item['inviter_username'] = '<img src=x onerror=alert(1)>'
        response = self.client.get('/notificaciones/')
        self.assertContains(response, '&lt;script&gt;')
        for secret in ('<script>alert', 'private-jwt', settings.SUPABASE_SECRET_KEY, 'auth.users'):
            self.assertNotContains(response, secret)

    def test_anonymous_cannot_access_invitation_or_notifications(self):
        self.current.return_value = None
        for method, url in [('get', self.invite_url), ('post', self.invite_url),
                            ('get', '/notificaciones/'), ('post', self.accept_url),
                            ('post', self.reject_url), ('post', self.read_url)]:
            response = getattr(self.client, method)(url)
            self.assertTrue(response.url.startswith('/login/?next='))
        self.create.assert_not_called()
        self.notifications.assert_not_called()

    def test_expired_response_token_invalidates_session(self):
        self.accept.side_effect = SessionExpired()
        invalidate = self.mock('apps.users.services.session_service.invalidate_session')
        response = self.client.post(self.accept_url)
        self.assertTrue(response.url.startswith('/login/?next='))
        invalidate.assert_called_once()

    def test_expired_badge_token_invalidates_session(self):
        self.badge.side_effect = SessionExpired()
        invalidate = self.mock('apps.users.services.session_service.invalidate_session')
        response = self.client.get('/notificaciones/')
        self.assertTrue(response.url.startswith('/login/?next='))
        invalidate.assert_called_once()
