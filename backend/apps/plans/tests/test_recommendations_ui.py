"""Integración del detalle y reintento, sin red ni cambios al dominio de lugares."""
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import plan_recommendation_service as service
from apps.plans.services.place_types import RecommendationError
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row, proposal_row

STAMP = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def confirmed_plan(**changes):
    return plan_row(**{"selected_proposal_id": 47, "confirmed_at": STAMP,
                      "scheduled_at": STAMP, **changes})


def confirmed_proposal():
    return proposal_row(date_pick=STAMP, latitude=-34.6, longitude=-58.38)


class RecommendationUITests(TestCase):
    def setUp(self):
        self.url = f"/planes/{PLAN_ID}/"
        self.retry_url = f"/planes/{PLAN_ID}/recomendaciones/reintentar/"
        fixtures = {
            "apps.users.services.session_service.current_user": {"id": USER_ID, "username": "Ana"},
            "apps.users.services.session_service.get_access_token": "ui-jwt",
            "apps.notifications.services.unread_count": 0,
            "apps.plans.views.plan_service.get_plan": confirmed_plan(),
            "apps.plans.views.group_service.get_group": {"id": GROUP_ID, "name": "Amigos", "role": "member"},
            "apps.plans.views.proposal_service.list_proposals": [confirmed_proposal()],
            "apps.plans.views.profile_service.usernames_for_ids": {USER_ID: "Ana"},
        }
        self.fixtures = {}
        for target, value in fixtures.items():
            patcher = patch(target, return_value=value)
            self.fixtures[target] = patcher.start()
            self.addCleanup(patcher.stop)

    @patch.object(service, "get_recommendations")
    @patch.object(service, "generate_recommendations")
    def test_confirmed_detail_has_one_section_without_server_generation(self, generate, read):
        response = self.client.get(self.url)
        self.assertContains(response, "Sugerencias con IA", count=1)
        self.assertContains(response, "data-recommendations", count=1)
        self.assertContains(response, self.retry_url)
        self.assertContains(response, "plan-recommendations.js")
        self.assertContains(response, 'src="/static/leaflet.js"', count=1)
        self.assertContains(response, 'data-suggestions-map role="region"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        generate.assert_not_called()
        read.assert_not_called()

    def test_section_absent_in_organizing_inactive_finished_or_unconfirmed(self):
        for changes in ({"selected_proposal_id": None, "confirmed_at": None},
                        {"status": False, "selected_proposal_id": None},
                        {"status": False}, {"confirmed_at": None}):
            self.fixtures["apps.plans.views.plan_service.get_plan"].return_value = confirmed_plan(**changes)
            response = self.client.get(self.url)
            self.assertNotContains(response, "data-recommendations")
            self.assertNotContains(response, "plan-recommendations.js")

    def test_confirmed_plan_loads_leaflet_even_if_proposal_list_empty(self):
        self.fixtures["apps.plans.views.proposal_service.list_proposals"].return_value = []
        response = self.client.get(self.url)
        self.assertContains(response, 'src="/static/leaflet.js"', count=1)
        self.assertContains(response, "data-recommendations", count=1)

    @override_settings(GEOAPIFY_API_KEY="key-that-must-never-reach-the-template")
    def test_frontend_receives_no_provider_key_or_raw_data(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, "key-that-must-never-reach-the-template")
        self.assertNotContains(response, "GEOAPIFY_API_KEY")
        self.assertNotContains(response, "api.geoapify.com")

    def test_outsider_cannot_open_detail(self):
        self.fixtures["apps.plans.views.group_service.get_group"].side_effect = GroupNotFound()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @patch.object(service, "get_recommendations")
    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_explicit_retry_uses_existing_replace_only_for_failed_or_pending(self, generate, read):
        for status in ("failed", "pending"):
            read.return_value = {"status": status, "items": []}
            response = self.client.post(self.retry_url, {"plan_id": "forged", "categories": "forged",
                                                       "latitude": "forged", "items": "forged"})
            self.assertEqual(response.status_code, 200)
            generate.assert_called_with("ui-jwt", PLAN_ID, replace=True)
        self.assertEqual(generate.call_count, 2)

    @patch.object(service, "get_recommendations", return_value={"status": "not_generated", "items": []})
    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_retry_after_request_failed_before_claim_uses_normal_generation(self, generate, read):
        self.assertEqual(self.client.post(self.retry_url).status_code, 200)
        generate.assert_called_once_with("ui-jwt", PLAN_ID)

    @patch.object(service, "get_recommendations")
    @patch.object(service, "generate_recommendations")
    def test_retry_does_not_replace_ready_or_generate_for_unconfirmed(self, generate, read):
        for status in ("ready", "not_confirmed"):
            read.return_value = {"status": status, "items": []}
            response = self.client.post(self.retry_url)
            self.assertEqual(response.json()["status"], status)
            self.assertIn("no-store", response.headers["Cache-Control"])
        generate.assert_not_called()

    @patch.object(service, "get_recommendations", side_effect=GroupNotFound())
    @patch.object(service, "generate_recommendations")
    def test_retry_rejects_outsider_before_replacement(self, generate, read):
        self.assertEqual(self.client.post(self.retry_url).status_code, 404)
        generate.assert_not_called()

    @patch.object(service, "generate_recommendations")
    def test_retry_requires_csrf_and_post(self, generate):
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.retry_url).status_code, 403)
        self.assertEqual(self.client.get(self.retry_url).status_code, 405)
        generate.assert_not_called()

    @patch.object(service, "get_recommendations", return_value={"status": "failed", "items": []})
    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_retry_accepts_real_csrf_token_from_detail(self, generate, read):
        client = Client(enforce_csrf_checks=True)
        client.get(self.url)
        token = client.cookies["csrftoken"].value
        response = client.post(self.retry_url, {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with("ui-jwt", PLAN_ID, replace=True)

    @patch.object(service, "get_recommendations", side_effect=RecommendationError("storage_unavailable"))
    def test_storage_failure_does_not_block_detail(self, read):
        self.assertEqual(self.client.post(self.retry_url).status_code, 503)
        self.assertContains(self.client.get(self.url), "Sugerencias con IA")
