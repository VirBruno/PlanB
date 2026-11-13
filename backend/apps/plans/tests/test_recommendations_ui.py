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
        self.assertContains(response, "Sugerencias con IA", count=2)
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

    def test_suggestions_are_in_workspace_before_decision_history(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertContains(response, "data-recommendations", count=1)
        self.assertLess(html.index("data-recommendations"), html.index("data-plan-drawer"))
        self.assertGreater(html.index("data-recommendations"), html.index('data-tool-panel="sugerencias"'))
        self.assertContains(response, 'aria-label="Lugares sugeridos" tabindex="0"')

    def test_confirmed_layout_has_three_initially_hidden_unique_tools(self):
        response = self.client.get(self.url)
        self.assertContains(response, "data-confirmed-layout", count=1)
        for name in ("clima", "gastos", "sugerencias"):
            self.assertContains(response, f'data-tool="{name}"', count=1)
            self.assertContains(response, f'data-tool-panel="{name}" hidden', count=1)
        for marker in ("data-plan-weather\n", "data-inline-expenses", "data-recommendations\n"):
            self.assertContains(response, marker, count=1)
        self.assertContains(response, "plan-confirmed-workspace.js", count=1)

    def test_confirmed_combat_shows_chosen_proposal_without_game_controls(self):
        self.fixtures["apps.plans.views.plan_service.get_plan"].return_value = confirmed_plan(election_method="combat")
        response = self.client.get(self.url + "?combat_game=uno")
        self.assertContains(response, "Propuesta ganadora:")
        self.assertNotContains(response, 'data-combat-choice=')
        self.assertNotContains(response, 'data-uno-mode-choice=')
        self.assertNotContains(response, 'data-combat-game')

    def test_drawer_has_collapsed_history_and_no_active_tools(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        drawer = html.split('data-plan-drawer', 1)[1].split('</dialog>', 1)[0]
        for section in ("proposals", "method", "voting", "result"):
            self.assertIn(f'data-decision-block="{section}"><summary>', drawer)
        for marker in ("data-plan-weather", "data-expenses-dialog", "data-recommendations"):
            self.assertNotIn(marker, drawer)
        self.assertContains(response, 'aria-controls="plan-details-drawer" aria-expanded="false"')

    def test_hero_includes_selected_proposal_information_and_process_action(self):
        proposals = self.fixtures["apps.plans.views.proposal_service.list_proposals"].return_value
        proposals[0].update(description="Merienda para compartir", budget_min=100, budget_max=200)
        response = self.client.get(self.url)
        hero = response.content.decode().split('pb-decision-panel--confirmed', 1)[1].split('<section class="pb-meeting-tools"', 1)[0]
        self.assertIn("Merienda para compartir", hero)
        self.assertIn("Presupuesto:", hero)
        self.assertIn("Ver ubicación confirmada", hero)
        self.assertIn("Ver proceso de decisión", hero)
        self.assertIn('class="pb-confirmed-author"', hero)
        self.assertIn('data-user-avatar', hero)
        self.assertIn('class="pb-confirmed-metadata"', hero)
        self.assertIn("Fecha y hora", hero)
        self.assertIn("Tipo de plan", hero)
        self.assertIn("data-proposal-weather", hero)
        self.assertIn("data-proposal-preview", hero)

    def test_selected_proposal_weather_and_map_are_only_in_hero(self):
        proposal = self.fixtures["apps.plans.views.proposal_service.list_proposals"].return_value[0]
        proposal["description"] = "Descripción exclusiva de la propuesta elegida"
        response = self.client.get(self.url)
        self.assertContains(response, proposal["description"], count=1)
        self.assertContains(response, "data-proposal-weather", count=1)
        self.assertContains(response, "data-proposal-preview", count=1)
        self.assertContains(response, "data-interactive-proposal-map", count=1)
        drawer = response.content.decode().split('data-plan-drawer', 1)[1].split('</dialog>', 1)[0]
        self.assertIn("pb-confirmed-history-reference", drawer)
        self.assertNotIn("data-proposal-weather", drawer)
        self.assertNotIn("data-proposal-preview", drawer)
        self.assertNotIn("data-interactive-proposal-map", drawer)

    def test_other_proposals_keep_weather_and_maps_in_history(self):
        proposals = self.fixtures["apps.plans.views.proposal_service.list_proposals"].return_value
        proposals.append({**confirmed_proposal(), "id": 48, "tittle": "Otra alternativa"})
        response = self.client.get(self.url)
        drawer = response.content.decode().split('data-plan-drawer', 1)[1].split('</dialog>', 1)[0]
        self.assertIn("Otra alternativa", drawer)
        self.assertEqual(drawer.count("data-proposal-weather"), 1)
        self.assertEqual(drawer.count("data-proposal-preview"), 1)
        self.assertContains(response, "data-proposal-weather", count=2)

    def test_organizing_and_finished_keep_original_primary_proposals_flow(self):
        for active_plan in (confirmed_plan(selected_proposal_id=None, confirmed_at=None), confirmed_plan(status=False)):
            self.fixtures["apps.plans.views.plan_service.get_plan"].return_value = active_plan
            response = self.client.get(self.url)
            self.assertNotContains(response, "data-confirmed-layout")
            self.assertNotContains(response, "plan-confirmed-workspace.js")
            self.assertContains(response, 'id="proposals-title"')
            self.assertContains(response, 'id="proposal-election"')
            self.assertContains(response, 'class="pb-expense-card"')

    def test_confirmed_owner_actions_and_expense_deep_link_remain_available(self):
        self.fixtures["apps.plans.views.group_service.get_group"].return_value["role"] = "owner"
        response = self.client.get(self.url + "?open=expenses")
        self.assertContains(response, f'/planes/{PLAN_ID}/editar/')
        self.assertContains(response, "Reabrir decisión")
        self.assertContains(response, "data-inline-expenses", count=1)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

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

    def test_summary_placeholder_is_hidden_and_before_results(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'data-suggestions-summary hidden></p>', count=1)
        html = response.content.decode()
        self.assertLess(html.index("data-suggestions-summary"), html.index("data-suggestions-results"))

    @patch.object(service, "get_recommendations", return_value={
        "status": "ready", "items": [], "can_refresh": True,
    })
    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_explicit_refresh_can_upgrade_legacy_ready(self, generate, read):
        self.assertEqual(self.client.post(self.retry_url).status_code, 200)
        generate.assert_called_once_with("ui-jwt", PLAN_ID, replace=True)

    @patch.object(service, "get_recommendations", return_value={
        "status": "ready", "items": [], "can_update": True,
    })
    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_explicit_refresh_can_update_stale_ready(self, generate, read):
        self.assertEqual(self.client.post(self.retry_url).status_code, 200)
        generate.assert_called_once_with("ui-jwt", PLAN_ID, replace=True)
