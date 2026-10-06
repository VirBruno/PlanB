from unittest.mock import patch

from django.test import TestCase

from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.plans.services.exceptions import PlanUnavailable
from apps.plans.services.proposal_exceptions import (
    ProposalAlreadyExists, ProposalUnavailable,
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

    @patch("apps.plans.views.plan_service.create_plan", return_value=PLAN_ID)
    @patch("apps.plans.views.group_service.get_group")
    def test_only_group_owner_can_create_a_group_plan(self, get_group, create):
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
    @patch("apps.plans.views.group_service.get_group", return_value={"role": "member"})
    def test_group_member_cannot_create_plan(self, get_group, create):
        response = self.client.get(f"/grupos/{GROUP_ID}/planes/nuevo/")
        self.assertEqual(response.status_code, 403)
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

    @patch("apps.plans.views.proposal_service.has_user_proposal", return_value=False)
    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "member"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_proposal_form_renders_map_search_and_geolocation_controls(self, get_plan, get_group, has_proposal):
        response = self.client.get(f"/planes/{PLAN_ID}/propuestas/nueva/")
        self.assertContains(response, "data-proposal-map")
        self.assertContains(response, "data-city-search")
        self.assertContains(response, "data-use-location")
        self.assertContains(response, "/static/leaflet.js")
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
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
    def test_member_sees_others_proposals_but_cannot_create_a_second(self, get_plan, get_group, list_proposals):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Propuesta de otra persona")
        self.assertContains(response, "Propuesta por <strong>Ana</strong>")
        self.assertContains(response, "Propuesta por <strong>Bruno</strong>")
        self.assertContains(response, 'class="proposal-item"', count=2)
        self.assertContains(response, "data-proposal-preview")
        self.assertContains(response, "data-lat=\"-34.7\"")
        self.assertContains(response, "/static/leaflet.js")
        self.assertNotContains(response, f"/planes/{PLAN_ID}/propuestas/nueva/")
        self.assertContains(response, "Ya compartiste tu propuesta para este plan.")
        self.assertEqual(
            response.headers["Referrer-Policy"], "strict-origin-when-cross-origin",
        )

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