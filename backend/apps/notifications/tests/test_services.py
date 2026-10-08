"""Contratos del SDK real sin red; PostgreSQL/RLS se prueban en supabase/tests."""
import json
from unittest.mock import patch

import httpx
from django.conf import settings
from django.test import SimpleTestCase

from apps.groups.services import invitation_service as invitations
from apps.groups.services.invitation_service import (
    InvitationConflict, InvitationNotFound, InvitationUnavailable, InvalidInvitation,
)
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.notifications import services
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

INVITATION_ID = '7c7d9c63-d685-4a60-a384-c2dbf10ecbe2'
NOTIFICATION_ID = '92a1c46e-ed20-486a-8f80-3eac5f7ec012'


def notification(**values):
    return {'id': NOTIFICATION_ID, 'type': 'group_invitation',
            'created_at': '2026-10-08T12:00:00Z', 'read_at': None,
            'invitation_id': INVITATION_ID, 'group_id': GROUP_ID,
            'group_name': 'Futboleros', 'inviter_username': 'Ana', 'status': 'pending', **values}


class InvitationSDKTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.responses, self.transports = [], [], []

        def handle(request):
            self.requests.append(request)
            self.assertTrue(self.responses, 'HTTP inesperado')
            status, payload = self.responses.pop(0)
            return httpx.Response(status, json=payload)

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(handle))
            self.transports.append(transport)
            return transport

        started = patch.object(clients, 'http_client', side_effect=factory)
        started.start()
        self.addCleanup(started.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(t.is_closed for t in self.transports))
        for request in self.requests:
            self.assertEqual(request.headers['apikey'], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers['authorization'], 'Bearer user-jwt')
            self.assertNotIn(settings.SUPABASE_SECRET_KEY, str(request.headers))

    def test_owner_search_uses_exact_username_rpc_and_minimal_fields(self):
        self.responses = [(200, [{'id': USER_ID, 'username': 'Ana', 'email': 'private@example.invalid'}])]
        people = invitations.search_invitees('user-jwt', GROUP_ID, 'ANA')
        self.assertEqual(people, [{'id': USER_ID, 'username': 'Ana', 'initials': 'AN'}])
        self.assertEqual(self.requests[0].method, 'GET')
        self.assertEqual(self.requests[0].url.path, '/rest/v1/rpc/search_group_invitees')
        self.assertEqual(self.requests[0].url.params['p_username'], 'ANA')
        self.assertEqual(self.requests[0].url.params['p_group_id'], GROUP_ID)

    def test_search_can_be_empty(self):
        self.responses = [(200, [])]
        self.assertEqual(invitations.search_invitees('user-jwt', GROUP_ID, 'Nobody'), [])

    def test_search_cannot_return_a_profile_list(self):
        self.responses = [(200, [{'id': USER_ID, 'username': 'Ana'}] * 2)]
        with self.assertRaises(InvitationUnavailable):
            invitations.search_invitees('user-jwt', GROUP_ID, 'Ana')

    def test_members_are_validated_and_sensitive_fields_dropped(self):
        self.responses = [(200, [{'id': USER_ID, 'username': 'Ana', 'role': 'member', 'secret': 'hidden'}])]
        people = invitations.list_members('user-jwt', GROUP_ID)
        self.assertEqual(people[0]['role_label'], 'Miembro')
        self.assertNotIn('secret', people[0])

    def test_pending_invitation_has_minimal_recipient_details(self):
        self.responses = [(200, [{'id': INVITATION_ID, 'invited_user_id': USER_ID,
                                  'username': 'Ana', 'created_at': '2026-10-08T12:00:00Z'}])]
        pending = invitations.list_pending('user-jwt', GROUP_ID)
        self.assertEqual(pending[0]['invited_user_id'], USER_ID)
        self.assertEqual(pending[0]['created_at'].year, 2026)

    def test_creation_is_one_atomic_rpc_without_actor_or_role(self):
        self.responses = [(200, INVITATION_ID)]
        self.assertEqual(invitations.create_invitation('user-jwt', GROUP_ID, USER_ID), INVITATION_ID)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, '/rest/v1/rpc/create_group_invitation')
        self.assertEqual(json.loads(self.requests[0].content), {'p_group_id': GROUP_ID, 'p_invited_user_id': USER_ID})

    def test_accept_sends_only_invitation_id_and_returns_group(self):
        self.responses = [(200, GROUP_ID)]
        self.assertEqual(invitations.accept_invitation('user-jwt', INVITATION_ID), GROUP_ID)
        self.assertEqual(self.requests[0].url.path, '/rest/v1/rpc/accept_group_invitation')
        self.assertEqual(json.loads(self.requests[0].content), {'p_invitation_id': INVITATION_ID})

    def test_reject_sends_only_invitation_id(self):
        self.responses = [(200, INVITATION_ID)]
        self.assertEqual(invitations.reject_invitation('user-jwt', INVITATION_ID), INVITATION_ID)
        self.assertEqual(self.requests[0].url.path, '/rest/v1/rpc/reject_group_invitation')
        self.assertEqual(json.loads(self.requests[0].content), {'p_invitation_id': INVITATION_ID})

    def test_no_token_never_opens_transport(self):
        operations = [lambda: invitations.search_invitees('', GROUP_ID, 'Ana'),
                      lambda: invitations.create_invitation('', GROUP_ID, USER_ID),
                      lambda: invitations.accept_invitation('', INVITATION_ID),
                      lambda: invitations.reject_invitation('', INVITATION_ID),
                      lambda: services.list_notifications(''), lambda: services.unread_count('')]
        for operation in operations:
            with self.assertRaises(SessionExpired):
                operation()
        self.assertFalse(self.requests)

    def test_invalid_ids_never_call_provider(self):
        for operation in (lambda: invitations.create_invitation('user-jwt', 'bad', USER_ID),
                          lambda: invitations.accept_invitation('user-jwt', 'bad'),
                          lambda: invitations.reject_invitation('user-jwt', None)):
            with self.assertRaises(InvitationNotFound):
                operation()
        self.assertFalse(self.requests)

    def test_safe_error_translation_and_no_payload_leak(self):
        for code, error in [('PT404', InvitationNotFound), ('PT409', InvitationConflict),
                            ('22023', InvalidInvitation), ('PGRST301', SessionExpired),
                            ('42501', InvitationUnavailable)]:
            self.responses = [(400, {'code': code, 'message': 'private secret', 'details': None, 'hint': None})]
            with self.assertRaises(error) as caught:
                invitations.accept_invitation('user-jwt', INVITATION_ID)
            self.assertNotIn('private secret', str(caught.exception))

    def test_self_and_existing_member_rejections_are_safe(self):
        for _ in range(2):
            self.responses = [(400, {'code': '22023', 'message': 'sensitive', 'details': None, 'hint': None})]
            with self.assertRaises(InvalidInvitation):
                invitations.create_invitation('user-jwt', GROUP_ID, USER_ID)

    def test_duplicate_pending_is_a_conflict(self):
        self.responses = [(409, {'code': 'PT409', 'message': 'sensitive', 'details': None, 'hint': None})]
        with self.assertRaises(InvitationConflict):
            invitations.create_invitation('user-jwt', GROUP_ID, USER_ID)

    def test_timeout_never_retries_response(self):
        with patch.object(httpx.Client, 'send', side_effect=httpx.ReadTimeout('private')) as send:
            with self.assertRaises(InvitationUnavailable):
                invitations.accept_invitation('user-jwt', INVITATION_ID)
        self.assertEqual(send.call_count, 1)

    def test_no_client_identity_arguments_for_accept_or_reject(self):
        for operation in (invitations.accept_invitation, invitations.reject_invitation):
            for field in ('group_id', 'user_id', 'invited_user_id', 'role'):
                with self.assertRaises(TypeError):
                    operation('user-jwt', INVITATION_ID, **{field: USER_ID})
        self.assertFalse(self.requests)

    def test_notification_list_uses_current_business_status(self):
        self.responses = [(200, [notification(status='accepted', payload={'status': 'pending'}, email='sensitive')])]
        item = services.list_notifications('user-jwt')['items'][0]
        self.assertEqual(item['status'], 'accepted')
        self.assertNotIn('payload', item)
        self.assertNotIn('email', item)

    def test_notifications_paginate_with_one_extra_row(self):
        self.responses = [(200, [notification()] * 13)]
        result = services.list_notifications('user-jwt', page=2)
        self.assertEqual(len(result['items']), 12)
        self.assertEqual(result['previous_page'], 1)
        self.assertEqual(result['next_page'], 3)
        self.assertEqual(self.requests[0].url.params['p_page'], '2')

    def test_notifications_invalid_page_resets_to_one(self):
        for page in (0, -1, 10001, 'bad'):
            self.responses = [(200, [])]
            result = services.list_notifications('user-jwt', page=page)
            self.assertEqual(result['page'], 1)

    def test_notifications_malformed_response_is_unavailable(self):
        for payload in ({'wrong': 'data'}, [notification(status='forged')], [notification(group_id='bad')],
                        [notification(created_at='bad')], [notification(read_at='bad')]):
            self.responses = [(200, payload)]
            with self.assertRaises(InvitationUnavailable):
                services.list_notifications('user-jwt')

    def test_mark_read_uses_only_notification_id(self):
        self.responses = [(200, NOTIFICATION_ID)]
        self.assertEqual(services.mark_read('user-jwt', NOTIFICATION_ID), NOTIFICATION_ID)
        self.assertEqual(json.loads(self.requests[0].content), {'p_notification_id': NOTIFICATION_ID})

    def test_unexpected_mutation_results_are_not_success(self):
        for payload in (None, 'bad', {'id': INVITATION_ID}):
            self.responses = [(200, payload)]
            with self.assertRaises(InvitationUnavailable):
                invitations.create_invitation('user-jwt', GROUP_ID, USER_ID)

    def test_unread_count_is_exact_head_query_subject_to_rls(self):
        def handle(request):
            self.assertEqual(request.method, 'HEAD')
            self.assertEqual(request.url.path, '/rest/v1/notifications')
            self.assertEqual(request.url.params['read_at'], 'is.null')
            self.assertNotIn('user_id', request.url.params)
            self.assertEqual(request.headers['authorization'], 'Bearer user-jwt')
            self.assertEqual(request.headers['apikey'], settings.SUPABASE_PUBLISHABLE_KEY)
            return httpx.Response(200, headers={'content-range': '*/2'})
        with patch.object(clients, 'http_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handle))):
            self.assertEqual(services.unread_count('user-jwt'), 2)

    def test_missing_count_does_not_claim_zero_unread(self):
        with patch.object(clients, 'http_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(200)))):
            with self.assertRaises(InvitationUnavailable):
                services.unread_count('user-jwt')
