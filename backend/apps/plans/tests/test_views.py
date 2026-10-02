from unittest.mock import patch

from django.test import TestCase

from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.plans.services.exceptions import PlanUnavailable


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
        self.current.start()
        self.token.start()
        self.addCleanup(self.current.stop)
        self.addCleanup(self.token.stop)

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
            f"/planes/{PLAN_ID}/eliminar/confirmar/",
        )
        for path in paths:
            self.assertTrue(self.client.get(path).url.startswith("/login/?next="))
        self.assertTrue(self.client.post(
            f"/grupos/{GROUP_ID}/planes/nuevo/", {"name": "Plan"},
        ).url.startswith("/login/?next="))
        self.assertTrue(self.client.post(f"/planes/{PLAN_ID}/eliminar/").url.startswith("/login/?next="))
        list_plans.assert_not_called()

    @patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "name": "Mi grupo", "role": "owner"})
    @patch("apps.plans.views.plan_service.get_plan", return_value=plan_row(created_by="another-user"))
    def test_group_owner_can_manage_plan_created_by_another_member(self, get_plan, get_group):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "Activo")
        self.assertContains(response, f"/planes/{PLAN_ID}/editar/")

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