"""Identidad estable entre vistas y referencia tentativa junto a las propuestas."""
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.users.templatetags.group_identity import group_variant


class GroupIdentityTests(SimpleTestCase):
    def test_identity_depends_on_uuid_not_list_position_or_case(self):
        first = render_to_string("components/group_art.html", {"group_id": GROUP_ID, "variant": 1})
        other_page = render_to_string("components/group_art.html", {"group_id": GROUP_ID.upper(), "variant": 3})
        self.assertEqual(first, other_page)

    def test_three_existing_motifs_are_available(self):
        variants = {group_variant(f"00000000-0000-0000-0000-{i:012d}") for i in range(1, 4)}
        self.assertEqual(variants, {1, 2, 3})

    def test_missing_identity_has_safe_visual_fallback(self):
        self.assertEqual(group_variant(None), 1)
        self.assertEqual(group_variant("not-a-uuid"), 1)


class TentativeReferenceTests(TestCase):
    def setUp(self):
        self.plan = plan_row(scheduled_at=timezone.now(), weather_province="buenos-aires",
                             weather_city_id=3433955, weather_city_name="Mar del Plata")
        fixtures = {
            "apps.users.services.session_service.current_user": {"id": USER_ID, "username": "Ana"},
            "apps.users.services.session_service.get_access_token": "ui-jwt",
            "apps.notifications.services.unread_count": 0,
            "apps.plans.views.plan_service.get_plan": self.plan,
            "apps.plans.views.group_service.get_group": {"id": GROUP_ID, "name": "Amigos", "role": "member"},
            "apps.plans.views.proposal_service.list_proposals": [],
            "apps.plans.views.profile_service.usernames_for_ids": {},
        }
        for name, value in fixtures.items():
            patcher = patch(name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_reference_follows_proposals_heading_and_keeps_weather_hooks(self):
        response = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(response, "PREFERENCIA TENTATIVA")
        self.assertContains(response, "La fecha y el lugar definitivos se eligen al confirmar una propuesta.")
        self.assertContains(response, "data-plan-schedule-summary", count=1)
        self.assertContains(response, 'data-city-id="3433955"')
        html = response.content.decode()
        self.assertGreater(html.index('class="pb-schedule-summary pb-tentative-reference"'), html.index('id="proposals-title"'))
        self.assertGreater(html.index('id="proposals-title"'), html.index('class="group-meta"'))

    def test_confirmed_plan_does_not_show_tentative_reference(self):
        self.plan["selected_proposal_id"] = 47
        self.assertNotContains(self.client.get(f"/planes/{PLAN_ID}/"), "data-plan-schedule-summary")
