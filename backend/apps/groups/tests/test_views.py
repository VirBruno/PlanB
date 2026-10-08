from unittest.mock import patch
from datetime import datetime, timezone

from django.conf import settings
from django.test import Client, TestCase

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable, InvalidGroup
from apps.groups.tests.test_sdk_contracts import GROUP_ID, USER_ID
from apps.users.services.exceptions import SessionExpired


class GroupViewsTests(TestCase):
    def setUp(self):
        self.current = patch("apps.users.services.session_service.current_user", return_value={"id": USER_ID, "username": "Ana"})
        self.token = patch("apps.users.services.session_service.get_access_token", return_value="private-jwt")
        self.current.start()
        self.token.start()
        self.addCleanup(self.current.stop)
        self.addCleanup(self.token.stop)
        self.url = "/grupos/nuevo/"
        self.detail_url = f"/grupos/{GROUP_ID}/"

    @patch("apps.groups.views.group_service.create_group")
    def test_get_form_is_read_only_and_accessible(self, create):
        response = self.client.get(self.url)
        self.assertContains(response, 'for="id_name"')
        self.assertContains(response, 'for="id_description"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn("no-store", response["Cache-Control"])
        create.assert_not_called()

    @patch("apps.groups.views.group_service.create_group")
    def test_invalid_form_never_calls_service(self, create):
        response = self.client.post(self.url, {"name": "  ", "description": "Texto"})
        self.assertContains(response, "Texto")
        self.assertContains(response, "data-error-summary")
        create.assert_not_called()

    @patch("apps.groups.views.group_service.create_group", return_value=GROUP_ID)
    def test_create_redirects_and_ignores_forged_owner(self, create):
        response = self.client.post(self.url, {
            "name": "  Escapada ", "description": "", "user_id": "attacker",
            "created_by": "attacker", "role": "member",
        })
        self.assertRedirects(response, self.detail_url, fetch_redirect_response=False)
        create.assert_called_once_with("private-jwt", name="Escapada", description="")

    @patch("apps.groups.views.group_service.create_group", side_effect=GroupUnavailable())
    def test_failed_creation_preserves_inputs_and_warns_about_uncertainty(self, create):
        response = self.client.post(self.url, {"name": "Escapadas", "description": "Montaña"})
        self.assertContains(response, "Escapadas", status_code=503)
        self.assertContains(response, "Montaña", status_code=503)
        self.assertContains(response, "Revisá Mis grupos", status_code=503)
        self.assertEqual(create.call_count, 1)

    @patch("apps.groups.views.group_service.create_group", side_effect=InvalidGroup())
    def test_provider_validation_is_form_error(self, create):
        response = self.client.post(self.url, {"name": "Escapadas"})
        self.assertContains(response, "Revisá el nombre")

    @patch("apps.groups.views.group_service.get_group", side_effect=GroupNotFound())
    def test_unavailable_group_is_404(self, get):
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)

    @patch("apps.groups.views.group_service.get_group", side_effect=GroupUnavailable())
    def test_detail_outage_has_retry(self, get):
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Volver a intentar", status_code=503)

    @patch("apps.groups.views.plan_service.list_plans", return_value={"items": [], "page": 1, "has_next": False})
    @patch("apps.groups.views.group_service.get_group")
    def test_detail_escapes_content_and_never_exposes_tokens(self, get, list_plans):
        get.return_value = {
            "id": GROUP_ID, "role": "member",
            "name": "<script>alert(1)</script>", "description": "<img src=x onerror=alert(1)>",
            "role_label": "Miembro", "created_at": None,
        }
        response = self.client.get(self.detail_url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")
        self.assertContains(response, "Miembro")
        for secret in ("private-jwt", settings.SUPABASE_SECRET_KEY, settings.SUPABASE_PUBLISHABLE_KEY):
            self.assertNotContains(response, secret)

    @patch("apps.groups.views.plan_service.list_plans")
    @patch("apps.groups.views.group_service.get_group")
    def test_detail_groups_plan_cards_by_creation_date_and_shows_owner_plus(self, get, list_plans):
        get.return_value = {
            "id": GROUP_ID, "name": "Escapadas", "description": "",
            "role": "owner", "role_label": "Owner", "created_at": None,
        }
        list_plans.side_effect = [{
            "items": [
                {"id": "9f3bb3a8-50ea-4f9d-a7c5-80e5ed548e8e", "name": "Plan nuevo",
                 "description": "Una salida", "status": True,
                 "created_at": datetime(2026, 10, 2, 15, tzinfo=timezone.utc)},
            ],
            "page": 1, "next_page": 2,
        }, {
            "items": [
                {"id": "4b5d2158-2bf5-4be7-8407-79022cb079fd", "name": "Plan anterior",
                 "description": "Otra salida", "status": False,
                 "created_at": datetime(2026, 10, 1, 15, tzinfo=timezone.utc)},
            ],
            "page": 2, "next_page": None,
        }]
        response = self.client.get(self.detail_url)
        content = response.content.decode()
        self.assertLess(content.index("Plan nuevo"), content.index("Plan anterior"))
        from html.parser import HTMLParser

        class PanelParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.sections, self.plans_inside_panel = [], False

            def handle_starttag(self, tag, attrs):
                if tag == 'section':
                    classes = dict(attrs).get('class', '').split()
                    if 'plans-section' in classes:
                        self.plans_inside_panel = any('group-panel' in parent for parent in self.sections)
                    self.sections.append(classes)

            def handle_endtag(self, tag):
                if tag == 'section':
                    self.sections.pop()

        parser = PanelParser()
        parser.feed(content)
        self.assertTrue(parser.plans_inside_panel)
        self.assertContains(response, 'aria-label="Crear plan"')
        self.assertContains(response, f'href="/grupos/{GROUP_ID}/planes/nuevo/"')
        self.assertEqual(len(response.context["plans_by_date"]), 2)
        self.assertEqual(list_plans.call_count, 2)
        self.assertEqual(list_plans.call_args_list[0].kwargs, {"page": 1, "group_id": GROUP_ID})
        self.assertEqual(list_plans.call_args_list[1].kwargs, {"page": 2, "group_id": GROUP_ID})

    @patch("apps.groups.views.plan_service.list_plans", return_value={"items": [], "page": 1, "has_next": False})
    @patch("apps.groups.views.group_service.get_group")
    def test_empty_group_shows_text_create_action_only_to_owner(self, get, list_plans):
        get.return_value = {
            "id": GROUP_ID, "name": "Escapadas", "description": "",
            "role": "owner", "role_label": "Owner", "created_at": None,
        }
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Este grupo todavía no tiene planes.")
        self.assertContains(response, "Crear plan")
        self.assertNotContains(response, 'aria-label="Crear plan"')
        self.assertEqual(
            response.content.count(f'href="/grupos/{GROUP_ID}/planes/nuevo/"'.encode()),
            1,
        )

    @patch("apps.groups.views.plan_service.list_plans", return_value={"items": [], "page": 1, "has_next": False})
    @patch("apps.groups.views.group_service.get_group")
    def test_member_empty_state_has_create_action_without_global_nav_link(self, get, list_plans):
        get.return_value = {
            "id": GROUP_ID, "name": "Escapadas", "description": "",
            "role": "member", "role_label": "Miembro", "created_at": None,
        }
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Este grupo todavía no tiene planes.")
        self.assertContains(response, "Crear plan")
        self.assertNotContains(response, 'href="/planes/"')

    @patch("apps.groups.views.group_service.create_group")
    def test_csrf_rejection_does_not_create(self, create):
        response = Client(enforce_csrf_checks=True).post(self.url, {"name": "Grupo"})
        self.assertEqual(response.status_code, 403)
        create.assert_not_called()

    @patch("apps.groups.views.group_service.create_group", return_value=GROUP_ID)
    def test_valid_browser_csrf_creates_group(self, create):
        browser = Client(enforce_csrf_checks=True)
        browser.get(self.url)
        response = browser.post(self.url, {"name": "Grupo", "csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 302)
        create.assert_called_once()

    @patch("apps.users.services.session_service.current_user", return_value=None)
    @patch("apps.groups.views.group_service.create_group")
    def test_anonymous_cannot_create_or_read(self, create, current):
        for path in (self.url, self.detail_url):
            self.assertTrue(self.client.get(path).url.startswith("/login/?next="))
        self.assertTrue(self.client.post(self.url, {"name": "Grupo"}).url.startswith("/login/?next="))
        create.assert_not_called()

    @patch("apps.groups.views.group_service.create_group", side_effect=SessionExpired())
    @patch("apps.users.services.session_service.invalidate_session")
    def test_expired_jwt_invalidates_session_and_returns_to_login(self, invalidate, create):
        response = self.client.post(self.url, {"name": "Grupo"})
        self.assertTrue(response.url.startswith("/login/?next="))
        invalidate.assert_called_once()

    def test_detail_is_read_only(self):
        self.assertEqual(self.client.post(self.detail_url).status_code, 405)

    @patch("apps.users.views.group_service.list_groups")
    def test_dashboard_lists_memberships_and_creation_action(self, groups):
        groups.return_value = {"items": [{"id": GROUP_ID, "name": "Grupo ajeno", "description": ""}], "page": 1}
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Grupo ajeno")
        self.assertContains(response, self.detail_url)
        self.assertContains(response, 'href="/grupos/nuevo/"')
        self.assertNotContains(response, "Todavía no participás")

    @patch("apps.users.views.group_service.list_groups", side_effect=GroupUnavailable())
    def test_dashboard_outage_is_not_empty_state(self, groups):
        response = self.client.get("/dashboard/")
        self.assertContains(response, "No pudimos cargar tus grupos", status_code=503)
        self.assertNotContains(response, "Todavía no participás", status_code=503)

    @patch("apps.users.views.group_service.list_groups")
    def test_dashboard_pagination(self, groups):
        groups.return_value = {"items": [], "page": 2, "previous_page": 1, "next_page": 3}
        response = self.client.get("/dashboard/?page=2")
        groups.assert_called_once_with("private-jwt", page=2)
        self.assertContains(response, "?page=1")
        self.assertContains(response, "?page=3")
        self.assertNotContains(response, "Todavía no participás")

    @patch("apps.users.views.group_service.list_groups", return_value={"items": [], "page": 1})
    def test_dashboard_empty_and_invalid_page(self, groups):
        response = self.client.get("/dashboard/?page=invalid")
        groups.assert_called_once_with("private-jwt", page=1)
        self.assertContains(response, "Crear mi primer grupo")


class GroupNavigationFlowTests(TestCase):
    def test_login_create_detail_dashboard_with_real_session_and_mock_http(self):
        import json
        from datetime import timedelta
        from django.utils import timezone
        import httpx
        from apps.users.models import SupabaseSession
        from apps.users.services import clients
        from apps.users.services.types import AuthSession
        from apps.groups.tests.test_sdk_contracts import group_row

        browser = Client(enforce_csrf_checks=True)
        tokens = AuthSession(
            access_token="flow-jwt", refresh_token="flow-refresh", user_id=USER_ID,
            expires_at=int((timezone.now() + timedelta(hours=1)).timestamp()),
        )
        remote = {}
        requests = []

        def handle(request):
            requests.append(request)
            self.assertEqual(request.headers["apikey"], settings.SUPABASE_PUBLISHABLE_KEY)
            self.assertEqual(request.headers["authorization"], "Bearer flow-jwt")
            if request.url.path.endswith('/notifications'):
                return httpx.Response(200, headers={'content-range': '*/0'})
            if request.url.path.endswith(('/list_group_members', '/list_pending_group_invitations')):
                return httpx.Response(200, json=[])
            if request.url.path.endswith("/rpc/create_group"):
                payload = json.loads(request.content)
                remote["group"] = group_row(name=payload["p_name"], description=payload["p_description"])
                return httpx.Response(200, json=GROUP_ID)
            if request.url.path.endswith("/group_members"):
                return httpx.Response(200, json=[{"role": "owner"}])
            if request.url.path.endswith("/plans"):
                return httpx.Response(200, json=[])
            self.assertTrue(request.url.path.endswith("/groups"))
            return httpx.Response(200, json=[remote["group"]] if remote else [])

        with patch("apps.users.services.auth_service.sign_in", return_value=tokens), \
             patch("apps.users.services.auth_service.get_user", return_value=USER_ID), \
             patch("apps.users.services.profile_service.get_profile", return_value={"id": USER_ID, "username": "Ana"}), \
             patch.object(clients, "http_client", side_effect=lambda: httpx.Client(transport=httpx.MockTransport(handle))):
            browser.get("/login/")
            browser.post("/login/", {
                "identifier": "Ana", "password": "Password12345",
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            })
            self.assertEqual(SupabaseSession.objects.get().access_token, "flow-jwt")
            self.assertContains(browser.get("/dashboard/"), "Crear mi primer grupo")
            browser.get("/grupos/nuevo/")
            response = browser.post("/grupos/nuevo/", {
                "name": "Viaje de prueba", "description": "Sin red",
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            }, follow=True)
            self.assertContains(response, "Viaje de prueba")
            self.assertContains(response, "Owner")
            self.assertNotContains(response, "flow-jwt")
            dashboard = browser.get("/dashboard/")
            self.assertContains(dashboard, "Viaje de prueba")
            self.assertNotContains(dashboard, "Todavía no participás")
            self.assertEqual(sum(r.method == "POST" for r in requests), 1)
