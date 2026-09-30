"""Permisos de edición/eliminación y navegación sin red."""
from unittest.mock import patch

from django.test import Client, TestCase

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.users.services.exceptions import SessionExpired


class GroupManagementTests(TestCase):
    def setUp(self):
        self.detail_url = f"/grupos/{GROUP_ID}/"
        self.edit_url = self.detail_url + "editar/"
        self.confirm_url = self.detail_url + "eliminar/confirmar/"
        self.delete_url = self.detail_url + "eliminar/"
        self.group = {
            "id": GROUP_ID, "name": "Escapadas", "description": "Montaña",
            "role": "owner", "role_label": "Owner", "created_by": USER_ID,
        }
        self.current = self.mock("apps.users.services.session_service.current_user", return_value={"id": USER_ID, "username": "Ana"})
        self.mock("apps.users.services.session_service.get_access_token", return_value="private-jwt")
        self.get = self.mock("apps.groups.views.group_service.get_group", return_value=self.group)
        self.update = self.mock("apps.groups.views.group_service.update_group", return_value=GROUP_ID)
        self.delete = self.mock("apps.groups.views.group_service.delete_group", return_value=GROUP_ID)

    def mock(self, target, **kwargs):
        started = patch(target, **kwargs)
        result = started.start()
        self.addCleanup(started.stop)
        return result

    def test_owner_can_open_prefilled_edit_form(self):
        response = self.client.get(self.edit_url)
        self.assertContains(response, 'value="Escapadas"')
        self.assertContains(response, "Montaña")
        self.assertContains(response, "Guardar cambios")
        self.assertIn("no-store", response["Cache-Control"])
        self.update.assert_not_called()

    def test_owner_can_edit(self):
        response = self.client.post(self.edit_url, {"name": " Viaje ", "description": " Nuevos planes "})
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        self.update.assert_called_once_with("private-jwt", self.get.call_args.args[1], name="Viaje", description="Nuevos planes")

    def test_clearing_description_is_allowed(self):
        response = self.client.post(self.edit_url, {"name": "Viaje", "description": ""})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.update.call_args.kwargs["description"], "")

    def test_edit_uses_creation_validation(self):
        for data in ({"name": "  "}, {"name": "x" * 101}, {"name": "Viaje", "description": "x" * 1001}):
            with self.subTest(data=data):
                response = self.client.post(self.edit_url, data)
                self.assertContains(response, "data-error-summary")
        self.update.assert_not_called()

    def test_invalid_edit_preserves_input_and_escapes_html(self):
        response = self.client.post(self.edit_url, {"name": "", "description": "<script>alert(1)</script>"})
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)")
        self.update.assert_not_called()

    def test_forged_owner_role_id_are_not_forwarded(self):
        self.client.post(self.edit_url, {
            "name": "Viaje", "created_by": "attacker", "user_id": "attacker",
            "owner_id": "attacker", "id": "other", "role": "owner",
        })
        self.assertEqual(self.update.call_args.kwargs, {"name": "Viaje", "description": ""})
        self.assertEqual(str(self.update.call_args.args[1]), GROUP_ID)

    def test_member_cannot_edit_even_if_created_by_matches(self):
        self.group["role"] = "member"
        for method in ("get", "post"):
            response = getattr(self.client, method)(self.edit_url, {"name": "Forged"})
            self.assertEqual(response.status_code, 403)
        self.update.assert_not_called()

    def test_member_cannot_delete_or_open_confirmation(self):
        self.group["role"] = "member"
        self.assertEqual(self.client.get(self.confirm_url).status_code, 403)
        self.assertEqual(self.client.post(self.delete_url, {"role": "owner", "user_id": USER_ID}).status_code, 403)
        self.delete.assert_not_called()

    def test_outsider_cannot_edit_or_delete(self):
        self.get.side_effect = GroupNotFound()
        for path, method in ((self.edit_url, "get"), (self.edit_url, "post"), (self.confirm_url, "get"), (self.delete_url, "post")):
            self.assertEqual(getattr(self.client, method)(path, {"name": "Test"}).status_code, 404)
        self.update.assert_not_called()
        self.delete.assert_not_called()

    def test_anonymous_redirects_to_login(self):
        self.current.return_value = None
        for path, method in ((self.edit_url, "get"), (self.edit_url, "post"), (self.confirm_url, "get"), (self.delete_url, "post")):
            response = getattr(self.client, method)(path, {"name": "Test"})
            self.assertTrue(response.url.startswith("/login/?next="))
        self.update.assert_not_called()
        self.delete.assert_not_called()
        self.get.assert_not_called()

    def test_owner_sees_edit_and_confirm_links_only(self):
        response = self.client.get(self.detail_url)
        self.assertContains(response, self.edit_url)
        self.assertContains(response, self.confirm_url)
        self.assertNotContains(response, f'href="{self.delete_url}"')
        self.group["role"] = "member"
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, "Editar grupo")
        self.assertNotContains(response, "Eliminar grupo")

    def test_confirmation_names_group_and_warns_without_deleting(self):
        response = self.client.get(self.confirm_url)
        self.assertContains(response, "Escapadas")
        self.assertContains(response, "permanente")
        self.assertContains(response, "todas sus membresías")
        self.assertContains(response, f'action="{self.delete_url}"')
        self.assertContains(response, "Cancelar y volver")
        self.delete.assert_not_called()

    def test_delete_rejects_get_and_other_methods(self):
        for method in ("get", "head", "put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(self.delete_url).status_code, 405)
        self.delete.assert_not_called()
        self.get.assert_not_called()

    def test_confirmation_does_not_accept_post(self):
        self.assertEqual(self.client.post(self.confirm_url).status_code, 405)
        self.delete.assert_not_called()

    def test_owner_delete_redirects_to_dashboard(self):
        response = self.client.post(self.delete_url, {"group_id": "other", "role": "member"})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(self.delete.call_args.args[0], "private-jwt")
        self.assertEqual(str(self.delete.call_args.args[1]), GROUP_ID)
        self.assertFalse(self.delete.call_args.kwargs)

    def test_edit_and_delete_require_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        for path in (self.edit_url, self.delete_url):
            self.assertEqual(browser.post(path, {"name": "Test"}).status_code, 403)
        self.update.assert_not_called()
        self.delete.assert_not_called()

    def test_confirmation_then_csrf_post_deletes(self):
        browser = Client(enforce_csrf_checks=True)
        browser.get(self.confirm_url)
        response = browser.post(self.delete_url, {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
        self.assertEqual(response.url, "/dashboard/")
        self.delete.assert_called_once()

    def test_edit_with_browser_csrf(self):
        browser = Client(enforce_csrf_checks=True)
        browser.get(self.edit_url)
        response = browser.post(self.edit_url, {"name": "Viaje", "csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
        self.assertEqual(response.url, self.detail_url)
        self.update.assert_called_once()

    def test_rpc_rechecks_authorization_after_view_read(self):
        self.update.side_effect = GroupNotFound()
        self.delete.side_effect = GroupNotFound()
        self.assertEqual(self.client.post(self.edit_url, {"name": "Viaje"}).status_code, 404)
        self.assertEqual(self.client.post(self.delete_url).status_code, 404)

    def test_provider_validation_preserves_form(self):
        self.update.side_effect = InvalidGroup()
        response = self.client.post(self.edit_url, {"name": "Viaje"})
        self.assertContains(response, "Revisá el nombre")
        self.assertContains(response, 'value="Viaje"')

    def test_write_failures_are_safe_and_do_not_retry(self):
        self.update.side_effect = GroupUnavailable()
        response = self.client.post(self.edit_url, {"name": "Viaje"})
        self.assertContains(response, "Revisá el detalle", status_code=503)
        self.update.assert_called_once()
        self.delete.side_effect = GroupUnavailable()
        response = self.client.post(self.delete_url)
        self.assertContains(response, "Revisá Mis grupos", status_code=503)
        self.delete.assert_called_once()
        self.assertNotContains(response, "private-jwt", status_code=503)

    def test_read_outage_does_not_link_to_post_only_route(self):
        self.get.side_effect = GroupUnavailable()
        response = self.client.post(self.delete_url)
        self.assertContains(response, f'href="{self.detail_url}"', status_code=503)
        self.assertNotContains(response, f'href="{self.delete_url}"', status_code=503)
        self.delete.assert_not_called()

    def test_expired_jwt_during_mutation_invalidates_session(self):
        invalidate = self.mock("apps.users.services.session_service.invalidate_session")
        for operation, path, data in ((self.update, self.edit_url, {"name": "Viaje"}), (self.delete, self.delete_url, {})):
            operation.side_effect = SessionExpired()
            response = self.client.post(path, data)
            self.assertTrue(response.url.startswith("/login/?next="))
        self.assertEqual(invalidate.call_count, 2)


class ManagementNavigationTests(TestCase):
    def test_edit_delete_and_dashboard_with_real_sdk_without_network(self):
        import json
        import httpx
        from apps.users.services import clients
        from apps.groups.tests.test_sdk_contracts import group_row

        browser = Client(enforce_csrf_checks=True)
        remote = {"group": group_row(), "membership": {"role": "owner"}}
        writes = []

        def handle(request):
            self.assertEqual(request.headers["authorization"], "Bearer flow-jwt")
            if request.method == "POST":
                payload = json.loads(request.content)
                writes.append(request.url.path)
                self.assertEqual(payload["p_group_id"], GROUP_ID)
                if request.url.path.endswith("/update_group"):
                    remote["group"]["name"] = payload["p_name"]
                    remote["group"]["description"] = payload["p_description"]
                elif request.url.path.endswith("/delete_group"):
                    remote.clear()  # Transporte simulado; CASCADE real se prueba en SQL.
                else:
                    raise AssertionError("RPC inesperada")
                return httpx.Response(200, json=GROUP_ID)
            if request.url.path.endswith("/group_members"):
                return httpx.Response(200, json=[remote["membership"]] if remote else [])
            self.assertTrue(request.url.path.endswith("/groups"))
            return httpx.Response(200, json=[remote["group"]] if remote else [])

        with patch("apps.users.services.session_service.current_user", return_value={"id": USER_ID, "username": "Ana"}), \
             patch("apps.users.services.session_service.get_access_token", return_value="flow-jwt"), \
             patch.object(clients, "http_client", side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handle))):
            root = f"/grupos/{GROUP_ID}/"
            browser.get(root + "editar/")
            response = browser.post(root + "editar/", {
                "name": "Nombre actualizado", "description": "Descripción actualizada",
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            }, follow=True)
            self.assertContains(response, "Nombre actualizado")
            self.assertContains(response, "Descripción actualizada")
            response = browser.get("/dashboard/")
            self.assertContains(response, "Nombre actualizado")
            response = browser.get(root + "eliminar/confirmar/")
            self.assertContains(response, "Nombre actualizado")
            self.assertEqual(len(writes), 1)
            response = browser.post(root + "eliminar/", {
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            }, follow=True)
            self.assertContains(response, "Crear mi primer grupo")
            self.assertNotContains(response, "Nombre actualizado")
            self.assertEqual(browser.get(root).status_code, 404)
            self.assertEqual(writes, ["/rest/v1/rpc/update_group", "/rest/v1/rpc/delete_group"])
