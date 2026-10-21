from unittest.mock import patch
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row, proposal_row
from apps.plans.services.exceptions import PlanUnavailable
from apps.plans.services.proposal_exceptions import (
    ElectionSchemaUnavailable, ProposalAlreadyExists, ProposalUnavailable,
)


class PlanViewsTests(TestCase):
    def setUp(self):
        self.current = patch(
            "apps.users.services.session_service.current_user",
            return_value={"id": USER_ID, "username": "Ana"},
        )
        self.token = patch(
            "apps.users.services.session_service.get_access_token",
            return_value="private-jwt",
        )
        self.usernames = patch(
            "apps.plans.views.profile_service.usernames_for_ids",
            return_value={
                USER_ID: "Ana",
                "e0c9b706-c892-4f13-8432-86ce10caf447": "Bruno",
            },
        )
        self.current.start()
        self.token.start()
        self.usernames.start()
        self.addCleanup(self.current.stop)
        self.addCleanup(self.token.stop)
        self.addCleanup(self.usernames.stop)

    @patch("apps.plans.views.plan_service.list_plans")
    def test_index_lists_group_plans_without_global_create_action(self, list_plans):
        list_plans.return_value = {"items": [plan_row()], "page": 1}
        response = self.client.get("/planes/")
        self.assertContains(response, "Escapada")
        self.assertNotContains(response, 'href="/planes/nuevo/"')
        list_plans.assert_called_once_with("private-jwt", page=1)

    @patch("apps.plans.views.plan_service.list_plans")
    def test_index_separates_active_and_inactive_plans_without_duplication(self, list_plans):
        active = plan_row(name="Juntada activa", status=True)
        inactive = plan_row(name="Viaje pendiente", status=False)
        inactive["id"] = "6e0e6647-32d8-454f-b2f2-847f2b20ef34"
        list_plans.return_value = {"items": [active, inactive], "page": 1}
        response = self.client.get("/planes/")
        self.assertContains(response, 'data-plan-section="active"')
        self.assertContains(response, 'data-plan-section="inactive"')
        self.assertContains(response, 'data-plan-search')
        self.assertContains(response, 'data-plan-sort')
        self.assertContains(response, 'data-plan-state="active"', count=1)
        self.assertContains(response, 'data-plan-state="inactive"', count=1)
        self.assertContains(response, "Juntada activa", count=1)
        self.assertContains(response, "Viaje pendiente", count=1)

    @patch("apps.plans.views.plan_service.create_plan", return_value=PLAN_ID)
    @patch("apps.plans.views.group_service.get_group")
    def test_group_owner_can_create_a_group_plan(self, get_group, create):
        get_group.return_value = {"id": GROUP_ID, "name": "Mi grupo", "role": "owner"}
        response = self.client.post(f"/grupos/{GROUP_ID}/planes/nuevo/", {
            "name": "Escapada", "description": "Viaje", "status": "true",
        })
        self.assertRedirects(response, f"/grupos/{GROUP_ID}/", fetch_redirect_response=False)
        create.assert_called_once_with(
            "private-jwt", created_by=USER_ID, group_id=GROUP_ID, name="Escapada",
            description="Viaje", status=True,
        )

    @patch("apps.plans.views.plan_service.create_plan", side_effect=PlanUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    def test_provider_permission_failure_is_not_returned_as_success(self, get_group, create):
        response = self.client.post(f"/grupos/{GROUP_ID}/planes/nuevo/", {
            "name": "Escapada", "description": "Viaje", "status": "true",
        })
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "No pudimos confirmar la creación", status_code=503)
        create.assert_called_once()

    @patch("apps.plans.views.plan_service.create_plan")
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    def test_group_member_can_open_create_plan(self, get_group, create):
        response = self.client.get(f"/grupos/{GROUP_ID}/planes/nuevo/")
        self.assertContains(response, 'name="name"')
        create.assert_not_called()

    @patch("apps.plans.views.plan_service.create_plan", return_value=PLAN_ID)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    def test_member_can_create_plan_with_server_identity(self, get_group, create):
        response = self.client.post(f"/grupos/{GROUP_ID}/planes/nuevo/", {
            'name': 'Salida', 'description': 'Parque', 'status': 'true',
            'created_by': 'forged', 'role': 'owner', 'group_id': 'forged',
        })
        self.assertRedirects(response, f'/grupos/{GROUP_ID}/', fetch_redirect_response=False)
        create.assert_called_once_with('private-jwt', created_by=USER_ID, group_id=GROUP_ID,
                                       name='Salida', description='Parque', status=True)

    @patch("apps.plans.views.plan_service.create_plan")
    @patch("apps.plans.views.group_service.get_group")
    def test_outsider_cannot_create_plan(self, get_group, create):
        from apps.groups.services.exceptions import GroupNotFound
        get_group.side_effect = GroupNotFound()
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)(f'/grupos/{GROUP_ID}/planes/nuevo/').status_code, 404)
        create.assert_not_called()

    @patch("apps.plans.views.plan_service.list_plans")
    @patch("apps.plans.views.group_service.get_group")
    def test_legacy_group_plan_list_redirects_to_group_detail(self, get_group, list_plans):
        get_group.return_value = {"id": GROUP_ID, "name": "Mi grupo", "role": "member"}
        response = self.client.get(f"/grupos/{GROUP_ID}/planes/")
        self.assertRedirects(response, f"/grupos/{GROUP_ID}/", fetch_redirect_response=False)
        list_plans.assert_not_called()

    @patch("apps.plans.views.plan_service.list_plans")
    @patch("apps.users.services.session_service.current_user", return_value=None)
    def test_anonymous_cannot_read_or_create_plans(self, current, list_plans):
        paths = (
            "/planes/", f"/grupos/{GROUP_ID}/planes/",
            f"/grupos/{GROUP_ID}/planes/nuevo/", f"/planes/{PLAN_ID}/",
            f"/planes/{PLAN_ID}/editar/",
            f"/planes/{PLAN_ID}/propuestas/nueva/",
            f"/planes/{PLAN_ID}/propuestas/47/editar/",
            f"/planes/{PLAN_ID}/eliminar/confirmar/",
        )
        for path in paths:
            self.assertTrue(self.client.get(path).url.startswith("/login/?next="))
        self.assertTrue(self.client.post(
            f"/grupos/{GROUP_ID}/planes/nuevo/", {"name": "Plan"},
        ).url.startswith("/login/?next="))
        self.assertTrue(self.client.post(f"/planes/{PLAN_ID}/eliminar/").url.startswith("/login/?next="))
        self.assertTrue(self.client.post(
            f"/planes/{PLAN_ID}/propuestas/47/eliminar/",
        ).url.startswith("/login/?next="))
        list_plans.assert_not_called()

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(created_by="another-user"))
    def test_group_owner_can_manage_plan_created_by_another_member(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Activo")
        self.assertContains(response, 'aria-label="Editar plan"')
        self.assertContains(response, 'aria-label="Eliminar plan"')
        self.assertContains(response, f"/planes/{PLAN_ID}/editar/")
        self.assertNotContains(response, "Editar plan <span")

    @patch("apps.plans.views.plan_service.delete_plan")
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(created_by="another-user"))
    def test_group_owner_can_delete_plan_created_by_another_member(self, get_plan, get_group, delete_plan):
        response = self.client.post(f"/planes/{PLAN_ID}/eliminar/")
        self.assertRedirects(response, f"/grupos/{GROUP_ID}/", fetch_redirect_response=False)
        delete_plan.assert_called_once_with("private-jwt", PLAN_ID)

    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(created_by=USER_ID))
    def test_group_member_cannot_edit_even_a_plan_they_created(self, get_plan, get_group):
        response = self.client.get(f"/planes/{PLAN_ID}/editar/")
        self.assertEqual(response.status_code, 403)

    def test_detail_rejects_post(self):
        self.assertEqual(self.client.post(f"/planes/{PLAN_ID}/").status_code, 405)

    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_admin_can_request_proposal_ideal(self, get_plan, get_group):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/ideal/")
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/?ideal=1#proposal-ideal-card",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_can_request_proposal_ideal(self, get_plan, get_group):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/ideal/")
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/?ideal=1#proposal-ideal-card",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.election_service.set_method", return_value="votes")
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_group_owner_can_choose_an_election_method(self, get_plan, get_group, set_method):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/metodo/", {"method": "votes"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposal-election",
            fetch_redirect_response=False,
        )
        set_method.assert_called_once_with(
            "private-jwt", plan_id=UUID(PLAN_ID), method="votes",
        )

    @patch("apps.plans.views.election_service.set_method", side_effect=ElectionSchemaUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_unmigrated_vote_mode_redirects_with_an_explanation(self, get_plan, get_group, set_method):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/metodo/", {"method": "votes"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposal-election",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.election_service.set_method", return_value="ideal")
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_choosing_ideal_method_opens_its_result_directly(self, get_plan, get_group, set_method):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/metodo/", {"method": "ideal"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/?ideal=1#proposal-ideal-card",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.election_service.set_method", side_effect=ProposalUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_ideal_method_still_opens_popup_when_method_storage_is_unavailable(self, get_plan, get_group, set_method):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/metodo/", {"method": "ideal"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/?ideal=1#proposal-ideal-card",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Merienda", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "budget_min": None, "budget_max": None, "votes": 0, "Score": 0},
    ])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_ideal_popup_renders_without_a_persisted_election_method(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/?ideal=1")
        self.assertContains(response, "data-ideal-dialog")
        self.assertContains(response, "proposal-ideal-data")

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Merienda", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "votes": 0, "Score": 0},
    ])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_group_owner_sees_three_choices_in_the_method_button(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, '<summary data-method-picker>Elija su Método de elección</summary>')
        self.assertContains(response, 'value="ideal"')
        self.assertContains(response, 'value="votes"')
        self.assertContains(response, 'value="combat"')

    @patch("apps.plans.views.election_service.set_method")
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_cannot_change_the_group_election_method(self, get_plan, get_group, set_method):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/metodo/", {"method": "combat"},
        )
        self.assertEqual(response.status_code, 403)
        set_method.assert_not_called()

    @patch("apps.plans.views.election_service.cast_vote", return_value=1)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_can_cast_a_vote_for_a_proposal(self, get_plan, get_group, cast_vote):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/47/votar/")
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposals-title",
            fetch_redirect_response=False,
        )
        cast_vote.assert_called_once_with(
            "private-jwt", plan_id=UUID(PLAN_ID), proposal_id=47,
        )

    @patch("apps.plans.views.election_service.remove_vote", return_value=47)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_can_remove_their_vote(self, get_plan, get_group, remove_vote):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/voto/eliminar/")
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposals-title",
            fetch_redirect_response=False,
        )
        remove_vote.assert_called_once_with("private-jwt", plan_id=UUID(PLAN_ID))

    @patch("apps.plans.views.election_service.cast_vote", side_effect=ElectionSchemaUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_vote_without_migration_returns_to_proposals_instead_of_503(self, get_plan, get_group, cast_vote):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/47/votar/")
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposals-title",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.election_service.save_score", return_value=14)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="combat"))
    def test_member_can_save_a_combat_score(self, get_plan, get_group, save_score):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/47/score/", {"score": "14"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposal-combat",
            fetch_redirect_response=False,
        )
        save_score.assert_called_once_with(
            "private-jwt", plan_id=UUID(PLAN_ID), proposal_id=47, score=14,
        )

    @patch("apps.plans.views.election_service.save_score", side_effect=ElectionSchemaUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_combat_score_without_migration_returns_instead_of_503(self, get_plan, get_group, save_score):
        response = self.client.post(
            f"/planes/{PLAN_ID}/propuestas/47/score/", {"score": "14"},
        )
        self.assertRedirects(
            response, f"/planes/{PLAN_ID}/#proposal-combat",
            fetch_redirect_response=False,
        )

    @patch("apps.plans.views.group_service.get_group")
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_outsider_cannot_calculate_proposal_ideal(self, get_plan, get_group):
        from apps.groups.services.exceptions import GroupNotFound
        get_group.side_effect = GroupNotFound()
        self.assertEqual(self.client.post(f"/planes/{PLAN_ID}/propuestas/ideal/").status_code, 404)

    def test_proposal_ideal_summary_calculates_radius_center_and_budget(self):
        from apps.plans.views import _proposal_ideal_summary

        result = _proposal_ideal_summary([
            {"longitude": -58.4, "latitude": -34.6,
             "budget_min": Decimal("100"), "budget_max": Decimal("200")},
            {"longitude": -58.3, "latitude": -34.6,
             "budget_min": Decimal("200"), "budget_max": Decimal("300")},
            {"longitude": -58.35, "latitude": -34.5,
             "budget_min": None, "budget_max": None},
        ])
        self.assertAlmostEqual(result["center"][0], -34.56666666666666)
        self.assertAlmostEqual(result["center"][1], -58.35)
        self.assertEqual(result["radius_meters"], 200)
        self.assertEqual(len(result["points"]), 3)
        self.assertEqual(result["ideal_budget"], Decimal("200.00"))
        self.assertEqual(result["budget_count"], 2)

    @patch("apps.plans.views.proposal_service.has_user_proposal", return_value=False)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_proposal_form_renders_map_search_and_geolocation_controls(self, get_plan, get_group, has_proposal):
        response = self.client.get(f"/planes/{PLAN_ID}/propuestas/nueva/")
        self.assertContains(response, "data-proposal-map")
        self.assertContains(response, "data-city-search")
        self.assertContains(response, "data-use-location")
        self.assertContains(response, "/static/leaflet.js")
        self.assertContains(response, '<legend>Presupuesto (opcional)</legend>')
        self.assertContains(response, 'for="id_budget_min"')
        self.assertContains(response, 'for="id_budget_max"')
        self.assertContains(response, 'step="0.01"')
        self.assertContains(response, 'Presupuesto (opcional)')
        self.assertEqual(
            response.headers["Referrer-Policy"], "strict-origin-when-cross-origin",
        )

    @patch("apps.plans.views.proposal_service.get_proposal", return_value={
        "id": 47, "created_by": "another-user", "plan_id": PLAN_ID,
        "posicion": {"type": "Point", "coordinates": [-58.38, -34.6]},
    })
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_cannot_edit_another_users_proposal(self, get_plan, get_group, get_proposal):
        response = self.client.get(f"/planes/{PLAN_ID}/propuestas/47/editar/")
        self.assertEqual(response.status_code, 403)

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_plan_detail_shows_create_proposal_action_to_members(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Propuestas")
        self.assertContains(response, f"/planes/{PLAN_ID}/propuestas/nueva/")

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Mi propuesta", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.38},
        {"id": 48, "created_by": "e0c9b706-c892-4f13-8432-86ce10caf447",
         "tittle": "Propuesta de otra persona", "type": "salida", "description": "",
         "date_pick": None, "latitude": -34.7, "longitude": -58.4},
    ])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="ideal"))
    def test_member_sees_others_proposals_but_cannot_create_a_second(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Propuesta de otra persona")
        self.assertContains(response, "Propuesta por <strong>Ana</strong>")
        self.assertContains(response, "Propuesta por <strong>Bruno</strong>")
        self.assertContains(response, 'class="proposal-item', count=2)
        self.assertContains(response, "data-proposal-preview")
        self.assertContains(response, "data-lat=\"-34.7\"")
        self.assertContains(response, "/static/leaflet.js")
        self.assertNotContains(response, f"/planes/{PLAN_ID}/propuestas/nueva/")
        self.assertNotContains(response, "Calcular propuesta ideal")
        self.assertContains(response, "Ver cálculo de ubicación y presupuesto")
        self.assertContains(response, "Ya compartiste tu propuesta para este plan.")
        self.assertEqual(
            response.headers["Referrer-Policy"], "strict-origin-when-cross-origin",
        )

    @patch("apps.plans.views.election_service.voted_proposal", return_value=None)
    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Merienda", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "budget_min": Decimal("100"), "budget_max": Decimal("200"), "votes": 0, "Score": 0},
    ])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="ideal"))
    def test_member_sees_ideal_proposal_card_and_map_after_calculation(self, get_plan, get_group, list_proposals, voted_proposal):
        response = self.client.get(f"/planes/{PLAN_ID}/?ideal=1")
        self.assertContains(response, "Propuesta ideal")
        self.assertContains(response, "Presupuesto ideal")
        self.assertContains(response, "150.00")
        self.assertContains(response, "data-ideal-dialog")
        self.assertContains(response, 'aria-label="Cerrar propuesta ideal"')
        self.assertContains(response, "data-ideal-map")
        self.assertContains(response, "proposal-ideal-data")
        self.assertContains(response, "/static/users/js/proposals.js?v=11")
        self.assertContains(response, "/static/users/css/proposals.css?v=5")

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Mi propuesta", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "votes": 4, "Score": 0},
        {"id": 48, "created_by": "e0c9b706-c892-4f13-8432-86ce10caf447",
         "tittle": "Otra propuesta", "type": "salida", "description": "",
         "date_pick": None, "latitude": -34.7, "longitude": -58.3,
         "votes": 2, "Score": 0},
    ])
    @patch("apps.plans.views.election_service.voted_proposal", return_value=None)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="votes"))
    def test_voting_mode_shows_one_vote_action_and_current_standings(self, get_plan, get_group, voted_proposal, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Votación")
        self.assertContains(response, "Propuesta campeona: <strong>Mi propuesta</strong>")
        self.assertContains(response, "Votar", count=2)
        self.assertContains(response, "4 voto")
        self.assertNotContains(response, "Calcular propuesta ideal")

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Mi propuesta", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "votes": 4, "Score": 0},
        {"id": 48, "created_by": "e0c9b706-c892-4f13-8432-86ce10caf447",
         "tittle": "Otra propuesta", "type": "salida", "description": "",
         "date_pick": None, "latitude": -34.7, "longitude": -58.3,
         "votes": 2, "Score": 0},
    ])
    @patch("apps.plans.views.election_service.voted_proposal", return_value=47)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="votes"))
    def test_voted_member_can_change_or_remove_their_vote(self, get_plan, get_group, voted_proposal, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Tu voto")
        self.assertContains(response, "Voto actual")
        self.assertContains(response, "Quitar voto")
        self.assertContains(response, "Votar", count=1)

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[
        {"id": 47, "created_by": USER_ID, "tittle": "Mi propuesta", "type": "juntada",
         "description": "", "date_pick": None, "latitude": -34.6, "longitude": -58.4,
         "votes": 0, "Score": 12},
        {"id": 48, "created_by": "e0c9b706-c892-4f13-8432-86ce10caf447",
         "tittle": "Otra propuesta", "type": "salida", "description": "",
         "date_pick": None, "latitude": -34.7, "longitude": -58.3,
         "votes": 0, "Score": 6},
    ])
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(election_method="combat"))
    def test_combat_mode_shows_game_and_leaderboard(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "data-combat-game")
        self.assertContains(response, "12 pts")
        self.assertContains(response, "Propuesta campeona: <strong>Mi propuesta</strong>")
        self.assertContains(response, "/static/users/js/proposal-combat.js")

    @patch("apps.plans.views.proposal_service.create_proposal", return_value=47)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_group_member_can_create_a_proposal_with_selected_point(self, get_plan, get_group, create):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/nueva/", {
            "tittle": "Merienda", "description": "En el parque", "date_pick": "",
            "type": "juntada", "position": '{"type":"Point","coordinates":[-58.38,-34.6]}',
            "city": "Buenos Aires",
        })
        self.assertRedirects(response, f"/planes/{PLAN_ID}/", fetch_redirect_response=False)
        create.assert_called_once()
        self.assertEqual(create.call_args.kwargs["created_by"], USER_ID)
        self.assertEqual(create.call_args.kwargs["position"], {
            "type": "Point", "coordinates": [-58.38, -34.6],
        })
        self.assertIsNone(create.call_args.kwargs["budget_min"])
        self.assertIsNone(create.call_args.kwargs["budget_max"])

    @patch("apps.plans.views.proposal_service.has_user_proposal", return_value=True)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_with_existing_proposal_is_redirected_from_create(self, get_plan, get_group, has_proposal):
        response = self.client.get(f"/planes/{PLAN_ID}/propuestas/nueva/")
        self.assertRedirects(response, f"/planes/{PLAN_ID}/", fetch_redirect_response=False)
        has_proposal.assert_called_once()
        self.assertEqual(str(has_proposal.call_args.kwargs["plan_id"]), PLAN_ID)
        self.assertEqual(has_proposal.call_args.kwargs["created_by"], USER_ID)

    @patch("apps.plans.views.proposal_service.create_proposal", side_effect=ProposalAlreadyExists())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_race_duplicate_is_shown_as_form_error(self, get_plan, get_group, create):
        response = self.client.post(f"/planes/{PLAN_ID}/propuestas/nueva/", {
            "tittle": "Merienda", "description": "", "date_pick": "",
            "type": "juntada", "position": '{"type":"Point","coordinates":[-58.38,-34.6]}',
            "city": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya creaste una propuesta para este plan.")
        create.assert_called_once()

    @patch("apps.plans.views.proposal_service.list_proposals", side_effect=ProposalUnavailable())
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_proposal_outage_does_not_show_an_empty_success_state(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertEqual(response.status_code, 503)


class ProposalBudgetViewsTests(TestCase):
    def mock(self, target, **kwargs):
        started = patch(target, **kwargs)
        mocked = started.start()
        self.addCleanup(started.stop)
        return mocked

    def setUp(self):
        self.mock('apps.users.services.session_service.current_user', return_value={'id': USER_ID, 'username': 'Ana'})
        self.mock('apps.users.services.session_service.get_access_token', return_value='private-jwt')
        self.mock('apps.notifications.services.unread_count', return_value=0)
        self.mock('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'name': 'Grupo', 'role': 'member'})
        self.mock('apps.plans.views.plan_service.get_plan', return_value=plan_row())
        self.mock('apps.plans.views.profile_service.usernames_for_ids', return_value={USER_ID: 'Ana'})
        self.create = self.mock('apps.plans.views.proposal_service.create_proposal', return_value=47)
        self.update = self.mock('apps.plans.views.proposal_service.update_proposal', return_value=47)
        self.proposal = proposal_row(budget_min=Decimal('10.50'), budget_max=Decimal('20.75'))
        self.get = self.mock('apps.plans.views.proposal_service.get_proposal', return_value=self.proposal)
        self.list = self.mock('apps.plans.views.proposal_service.list_proposals', return_value=[self.proposal])
        self.new_url = f'/planes/{PLAN_ID}/propuestas/nueva/'
        self.edit_url = f'/planes/{PLAN_ID}/propuestas/47/editar/'

    def data(self, **values):
        return {'tittle': 'Merienda', 'description': '', 'date_pick': '', 'type': 'juntada',
                'position': '{"type":"Point","coordinates":[-58.38,-34.6]}', **values}

    def test_create_submits_decimal_budget_and_authenticated_identity(self):
        response = self.client.post(self.new_url, self.data(budget_min='1.25', budget_max='2.75', created_by='forged'))
        self.assertRedirects(response, f'/planes/{PLAN_ID}/', fetch_redirect_response=False)
        self.assertEqual(self.create.call_args.kwargs['budget_min'], Decimal('1.25'))
        self.assertEqual(self.create.call_args.kwargs['budget_max'], Decimal('2.75'))
        self.assertEqual(self.create.call_args.kwargs['created_by'], USER_ID)

    def test_invalid_budget_preserves_input_and_never_calls_write_service(self):
        for method_url in (self.new_url, self.edit_url):
            response = self.client.post(method_url, self.data(budget_min='20.50', budget_max='10.25'))
            self.assertContains(response, 'value="20.50"')
            self.assertContains(response, 'value="10.25"')
            self.assertContains(response, 'El presupuesto hasta debe ser mayor o igual')
        self.create.assert_not_called()
        self.update.assert_not_called()

    def test_edit_form_prefills_current_budget(self):
        response = self.client.get(self.edit_url)
        self.assertContains(response, 'value="10.50"')
        self.assertContains(response, 'value="20.75"')
        self.assertEqual(response.context['form'].initial['budget_min'], Decimal('10.50'))
        self.assertEqual(response.context['form'].initial['budget_max'], Decimal('20.75'))
        self.update.assert_not_called()

    def test_edit_adds_budget_to_a_proposal_without_budget(self):
        self.proposal.update(budget_min=None, budget_max=None)
        response = self.client.post(self.edit_url, self.data(budget_min='10.50', budget_max='20.75'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.update.call_args.kwargs['budget_min'], Decimal('10.50'))
        self.assertEqual(self.update.call_args.kwargs['budget_max'], Decimal('20.75'))

    def test_edit_changes_one_limit_and_preserves_submitted_other_limit(self):
        response = self.client.post(self.edit_url, self.data(budget_min='15.25', budget_max='20.75'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.update.call_args.kwargs['budget_min'], Decimal('15.25'))
        self.assertEqual(self.update.call_args.kwargs['budget_max'], Decimal('20.75'))

    def test_edit_can_clear_either_or_both_limits(self):
        for minimum, maximum in (('', '20.75'), ('10.50', ''), ('', '')):
            response = self.client.post(self.edit_url, self.data(budget_min=minimum, budget_max=maximum))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.update.call_args.kwargs['budget_min'], Decimal(minimum) if minimum else None)
            self.assertEqual(self.update.call_args.kwargs['budget_max'], Decimal(maximum) if maximum else None)

    def test_member_sees_proposal_budget_but_cannot_edit_someone_elses(self):
        self.proposal['created_by'] = 'e0c9b706-c892-4f13-8432-86ce10caf447'
        response = self.client.get(f'/planes/{PLAN_ID}/')
        self.assertContains(response, 'Presupuesto')
        self.assertContains(response, '10,50 – 20,75')
        self.assertNotContains(response, self.edit_url)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.edit_url, self.data(budget_min='0', budget_max='10'))
            self.assertEqual(response.status_code, 403)
        self.update.assert_not_called()

    def test_detail_shows_partial_ranges_and_zero_without_currency(self):
        for minimum, maximum, expected in ((Decimal('0'), None, 'Desde 0,00'),
                                           (None, Decimal('0'), 'Hasta 0,00'),
                                           (Decimal('0'), Decimal('0'), '0,00 – 0,00')):
            self.proposal.update(budget_min=minimum, budget_max=maximum)
            response = self.client.get(f'/planes/{PLAN_ID}/')
            self.assertContains(response, expected)
            self.assertNotContains(response, '$')

    def test_detail_hides_undefined_budget(self):
        self.proposal.update(budget_min=None, budget_max=None)
        self.assertNotContains(self.client.get(f'/planes/{PLAN_ID}/'), 'Presupuesto')
