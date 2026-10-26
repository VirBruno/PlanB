"""Etapa 9A · Seguridad de finalizar/reactivar; no requiere API real de Supabase."""
from unittest.mock import patch
from uuid import UUID
from datetime import timedelta
from django.utils import timezone

from django.test import TestCase
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row


class PlanPhaseActionTests(TestCase):
    def setUp(self):
        self.session = patch(
            'apps.users.services.session_service.current_user',
            return_value={'id': USER_ID, 'username': 'Ana'},
        )
        self.token = patch(
            'apps.users.services.session_service.get_access_token',
            return_value='jwt-de-prueba',
        )
        self.session.start()
        self.token.start()
        self.addCleanup(self.session.stop)
        self.addCleanup(self.token.stop)

    def sample(self, *, role='owner', status=True, selected=11):
        # Mock del punto de acceso: la vista no depende de conexiones externas.
        row = plan_row(status=status, selected_proposal_id=selected,
                       scheduled_at=timezone.now() - timedelta(days=1))
        return row, {'id': GROUP_ID, 'role': role}

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_owner_finaliza_encuentro_confirmado(self, access, update):
        access.return_value = self.sample()
        response = self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/')
        self.assertEqual(response.status_code, 302)
        update.assert_called_once_with('jwt-de-prueba', plan_id=UUID(PLAN_ID),
                                       name=access.return_value[0]['name'],
                                       description=access.return_value[0]['description'], status=False)

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_no_se_finaliza_antes_de_fecha_programada(self, access, update):
        plan, group = self.sample()
        plan['scheduled_at'] = timezone.now() + timedelta(days=7)
        access.return_value = plan, group
        response = self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/')
        self.assertEqual(response.status_code, 302)
        update.assert_not_called()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_no_se_finaliza_sin_fecha_programada(self, access, update):
        plan, group = self.sample()
        plan['scheduled_at'] = None
        access.return_value = plan, group
        response = self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/')
        self.assertEqual(response.status_code, 302)
        update.assert_not_called()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_fecha_iso_y_timezone_en_validacion(self, access, update):
        plan, group = self.sample()
        plan['scheduled_at'] = (timezone.now() - timedelta(hours=1)).isoformat()
        access.return_value = plan, group
        response = self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/')
        self.assertEqual(response.status_code, 302)
        update.assert_called_once()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_miembro_no_puede_finalizar(self, access, update):
        access.return_value = self.sample(role='member')
        self.assertEqual(self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/').status_code, 403)
        update.assert_not_called()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_no_se_finaliza_sin_propuesta_elegida(self, access, update):
        access.return_value = self.sample(selected=None)
        self.assertEqual(self.client.post(f'/planes/{PLAN_ID}/encuentro/finalizar/').status_code, 302)
        update.assert_not_called()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_owner_reactiva_encuentro_finalizado(self, access, update):
        access.return_value = self.sample(status=False)
        response = self.client.post(f'/planes/{PLAN_ID}/encuentro/reactivar/')
        self.assertEqual(response.status_code, 302)
        update.assert_called_once_with('jwt-de-prueba', plan_id=UUID(PLAN_ID),
                                       name=access.return_value[0]['name'],
                                       description=access.return_value[0]['description'], status=True)

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_miembro_no_puede_reactivar(self, access, update):
        access.return_value = self.sample(role='member', status=False)
        self.assertEqual(self.client.post(f'/planes/{PLAN_ID}/encuentro/reactivar/').status_code, 403)
        update.assert_not_called()

    @patch('apps.plans.views.plan_service.update_plan')
    @patch('apps.plans.views._plan_and_group')
    def test_no_se_reactiva_encuentro_activo(self, access, update):
        access.return_value = self.sample(status=True)
        self.assertEqual(self.client.post(f'/planes/{PLAN_ID}/encuentro/reactivar/').status_code, 302)
        update.assert_not_called()

    def test_transiciones_solo_post(self):
        self.assertEqual(self.client.get(f'/planes/{PLAN_ID}/encuentro/finalizar/').status_code, 405)
        self.assertEqual(self.client.get(f'/planes/{PLAN_ID}/encuentro/reactivar/').status_code, 405)
