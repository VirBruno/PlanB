"""Navegación completa con CSRF y SDK real; sólo el transporte remoto se simula."""
import json
from unittest.mock import patch
from uuid import uuid4

import httpx
from django.conf import settings
from django.test import Client, TestCase

from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID, group_row
from apps.notifications.tests.test_services import notification
from apps.users.services import clients

GUEST_ID = '24030a78-4250-416a-83f8-556c7e260d57'
PLAN_ID = '080cbcee-8613-422d-83d1-c62c4be01852'


class InvitationNavigationTests(TestCase):
    def test_reject_reinvite_accept_dashboard_members_plans_and_member_creation(self):
        browser = Client(enforce_csrf_checks=True)
        state = {'actor': USER_ID, 'joined': False, 'invitations': [], 'plans': []}
        writes = []

        def handle(request):
            actor = state['actor']
            self.assertEqual(request.headers['apikey'], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers['authorization'], f'Bearer flow-{actor}')
            path = request.url.path.rsplit('/', 1)[-1]
            own = [i for i in state['invitations'] if actor == GUEST_ID]
            if path == 'notifications':
                self.assertEqual(request.method, 'HEAD')
                return httpx.Response(200, headers={'content-range': f"*/{sum(i['read_at'] is None for i in own)}"})
            if request.method == 'GET':
                if path == 'groups':
                    return httpx.Response(200, json=[group_row(name='Futboleros')] if actor == USER_ID or state['joined'] else [])
                if path == 'group_members':
                    return httpx.Response(200, json=[{'role': 'owner' if actor == USER_ID else 'member'}])
                if path == 'search_group_invitees':
                    self.assertEqual(request.url.params['p_username'], 'BRUNO')
                    return httpx.Response(200, json=[{'id': GUEST_ID, 'username': 'Bruno'}])
                if path == 'list_pending_group_invitations':
                    return httpx.Response(200, json=[{'id': i['invitation_id'], 'invited_user_id': GUEST_ID,
                        'username': 'Bruno', 'created_at': i['created_at']} for i in state['invitations'] if i['status'] == 'pending'])
                if path == 'list_group_members':
                    people = [{'id': USER_ID, 'username': 'Ana', 'role': 'owner'}]
                    if state['joined']:
                        people.append({'id': GUEST_ID, 'username': 'Bruno', 'role': 'member'})
                    return httpx.Response(200, json=people)
                if path == 'list_notifications':
                    return httpx.Response(200, json=own)
                if path == 'plans':
                    return httpx.Response(200, json=state['plans'])
                if path == 'proposals':
                    return httpx.Response(200, json=[])
            else:
                self.assertEqual(request.method, 'POST')
                payload = json.loads(request.content)
                writes.append((path, payload))
                if path == 'create_group_invitation':
                    self.assertEqual(actor, USER_ID)
                    self.assertEqual(payload, {'p_group_id': GROUP_ID, 'p_invited_user_id': GUEST_ID})
                    new_id = str(uuid4())
                    state['invitations'].insert(0, notification(id=str(uuid4()), invitation_id=new_id))
                    return httpx.Response(200, json=new_id)
                if path in ('accept_group_invitation', 'reject_group_invitation'):
                    self.assertEqual(actor, GUEST_ID)
                    self.assertEqual(set(payload), {'p_invitation_id'})
                    invitation = next(i for i in state['invitations'] if i['invitation_id'] == payload['p_invitation_id'])
                    self.assertEqual(invitation['status'], 'pending')
                    invitation['status'] = 'accepted' if path.startswith('accept') else 'rejected'
                    invitation['read_at'] = invitation['created_at']
                    if path.startswith('accept'):
                        state['joined'] = True
                    return httpx.Response(200, json=GROUP_ID if state['joined'] else invitation['invitation_id'])
                if path == 'plans':
                    self.assertEqual(actor, GUEST_ID)
                    self.assertTrue(state['joined'])
                    self.assertEqual(payload['created_by'], GUEST_ID)
                    self.assertEqual(payload['group_id'], GROUP_ID)
                    state['plans'].append({'id': PLAN_ID, 'created_at': '2026-10-08T12:00:00Z', **payload})
                    return httpx.Response(201, json=[{'id': PLAN_ID}])
            raise AssertionError(f'HTTP inesperado: {request.method} {path}')

        def post(url, data=None):
            return browser.post(url, {**(data or {}), 'csrfmiddlewaretoken': browser.cookies['csrftoken'].value}, follow=True)

        with patch('apps.users.services.session_service.current_user', side_effect=lambda request: {
                 'id': state['actor'], 'username': 'Ana' if state['actor'] == USER_ID else 'Bruno'}), \
             patch('apps.users.services.session_service.get_access_token', side_effect=lambda request: f"flow-{state['actor']}"), \
             patch.object(clients, 'http_client', side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handle))):
            invite_url = f'/grupos/{GROUP_ID}/invitar/'
            self.assertContains(browser.get(invite_url + '?username=BRUNO'), 'Enviar invitación')
            self.assertContains(post(invite_url, {'invited_user_id': GUEST_ID}), 'Invitaciones pendientes')
            first = state['invitations'][0]['invitation_id']
            state['actor'] = GUEST_ID
            self.assertNotContains(browser.get('/dashboard/'), 'Futboleros')
            self.assertContains(browser.get('/notificaciones/'), 'Notificaciones: 1 sin leer')
            self.assertContains(post(f'/notificaciones/invitaciones/{first}/rechazar/'), 'Invitación rechazada')
            self.assertFalse(state['joined'])
            state['actor'] = USER_ID
            post(invite_url, {'invited_user_id': GUEST_ID})
            second = state['invitations'][0]['invitation_id']
            state['actor'] = GUEST_ID
            response = post(f'/notificaciones/invitaciones/{second}/aceptar/', {'role': 'owner', 'user_id': USER_ID})
            self.assertContains(response, 'Invitación aceptada')
            self.assertContains(response, 'Invitación rechazada')
            self.assertNotContains(response, 'class="pb-notification-dot"')
            self.assertContains(browser.get('/dashboard/'), 'Futboleros')
            detail = browser.get(f'/grupos/{GROUP_ID}/')
            self.assertContains(detail, 'Bruno')
            self.assertContains(detail, 'Crear plan')
            self.assertNotContains(detail, 'Invitar integrante')
            self.assertEqual(browser.get(invite_url).status_code, 403)
            create_url = f'/grupos/{GROUP_ID}/planes/nuevo/'
            browser.get(create_url)
            self.assertContains(post(create_url, {'name': 'Salida de Bruno', 'description': 'Parque', 'status': 'true'}), 'Salida de Bruno')
            self.assertContains(browser.get(f'/planes/{PLAN_ID}/'), 'Salida de Bruno')
            self.assertEqual(browser.get(f'/grupos/{GROUP_ID}/editar/').status_code, 403)
            self.assertEqual(browser.post(f'/grupos/{GROUP_ID}/eliminar/', {
                'csrfmiddlewaretoken': browser.cookies['csrftoken'].value}).status_code, 403)
        self.assertEqual([path for path, payload in writes], ['create_group_invitation', 'reject_group_invitation',
                                                            'create_group_invitation', 'accept_group_invitation', 'plans'])
