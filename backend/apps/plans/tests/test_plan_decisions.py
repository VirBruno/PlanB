"""Etapa 8: permiso de owner y lectura de planes confirmados."""
from uuid import UUID
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase

from apps.plans.services import plan_service
from apps.plans.tests.test_services import PLAN_ID, USER_ID, GROUP_ID, plan_row


class ConfirmedPlanParsingTests(SimpleTestCase):
    def test_plan_confirmed_can_have_date_without_tentative_city(self):
        row = plan_row(
            scheduled_at="2026-10-31T20:30:00+00:00",
            weather_province=None,
            weather_city_id=None,
            weather_city_name=None,
            selected_proposal_id=11,
            confirmed_at="2026-10-22T18:00:00+00:00",
        )
        plan = plan_service._plan(row)
        self.assertEqual(plan["selected_proposal_id"], 11)
        self.assertEqual(plan["scheduled_at"].day, 31)
        self.assertTrue(plan["decision_schema"])


class PlanDecisionViewTests(TestCase):
    def setUp(self):
        self.user = patch(
            "apps.users.services.session_service.current_user",
            return_value={"id": USER_ID, "username": "Ana"},
        )
        self.token = patch(
            "apps.users.services.session_service.get_access_token",
            return_value="jwt-de-prueba",
        )
        self.user.start()
        self.token.start()
        self.addCleanup(self.user.stop)
        self.addCleanup(self.token.stop)

    @patch("apps.plans.views.plan_confirmation_service.confirm", return_value=11)
    @patch("apps.plans.views._proposal_plan")
    def test_owner_confirms_selected_proposal(self, access, confirm):
        access.return_value = (plan_row(), {"id": GROUP_ID, "role": "owner"})
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/11/confirmar/")
        self.assertEqual(response.status_code, 302)
        confirm.assert_called_once_with("jwt-de-prueba", plan_id=UUID(PLAN_ID), proposal_id=11)

    @patch("apps.plans.views.plan_confirmation_service.confirm")
    @patch("apps.plans.views._proposal_plan")
    def test_member_cannot_confirm(self, access, confirm):
        access.return_value = (plan_row(), {"id": GROUP_ID, "role": "member"})
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/11/confirmar/")
        self.assertEqual(response.status_code, 403)
        confirm.assert_not_called()

    @patch("apps.plans.views.plan_confirmation_service.reopen", return_value=True)
    @patch("apps.plans.views._proposal_plan")
    def test_owner_reopens_plan(self, access, reopen):
        access.return_value = (plan_row(), {"id": GROUP_ID, "role": "owner"})
        response = self.client.post(f"/planes/{PLAN_ID}/decision/reabrir/")
        self.assertEqual(response.status_code, 302)
        reopen.assert_called_once_with("jwt-de-prueba", plan_id=UUID(PLAN_ID))

    def test_confirmation_routes_do_not_accept_get(self):
        self.assertEqual(self.client.get(f"/planes/{PLAN_ID}/propuestas/11/confirmar/").status_code, 405)
        self.assertEqual(self.client.get(f"/planes/{PLAN_ID}/decision/reabrir/").status_code, 405)
