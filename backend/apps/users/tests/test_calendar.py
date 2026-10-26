"""Calendario personal: mes, eventos, permisos y navegación HTML sin red."""
from datetime import date, datetime, timezone as datetime_timezone
from unittest.mock import patch
from uuid import UUID

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.groups.services.exceptions import GroupNotFound, GroupUnavailable
from apps.plans.services import plan_service
from apps.plans.services.exceptions import PlanUnavailable
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.users.calendar_month import build_month, month_bounds, selected_month
from apps.users.services import calendar_service
from apps.users.services.exceptions import SessionExpired

FOREIGN_GROUP = "b4be205f-c6a2-45c8-9c6a-487b9201416e"
SECOND_PLAN = "6e0e6647-32d8-454f-b2f2-847f2b20ef34"
FIRST = date(2026, 10, 1)
TODAY = date(2026, 10, 26)


def meeting(**changes):
    return plan_service._plan(plan_row(
        **{"selected_proposal_id": 47, "scheduled_at": "2026-10-26T18:30:00Z",
           "confirmed_at": "2026-10-22T18:00:00Z", **changes},
    ))


def event(**changes):
    return {
        "id": PLAN_ID, "name": "Mates en el parque", "phase": "confirmed",
        "status_label": "Confirmado", "group_name": "Amigos",
        "starts_at": timezone.localtime(datetime(2026, 10, 26, 18, 30,
                                                tzinfo=datetime_timezone.utc)),
        **changes,
    }


class CalendarMonthTests(SimpleTestCase):
    def test_defaults_to_current_month(self):
        self.assertEqual(selected_month({}, today=TODAY), FIRST)

    def test_invalid_params_fall_back_to_current_month(self):
        for params in ({"month": "0"}, {"month": "13"}, {"month": "texto"},
                       {"month": "1.5"}, {"year": "0"}, {"year": "1899"},
                       {"year": "2101"}, {"year": "9999999999999999"},
                       {"year": ""}, {"month": None}, {"year": "2" * 5000}):
            with self.subTest(params=str(params)[:100]):
                self.assertEqual(selected_month(params, today=TODAY), FIRST)

    def test_valid_params_and_partial_params(self):
        self.assertEqual(selected_month({"year": "2027", "month": "1"}, today=TODAY),
                         date(2027, 1, 1))
        self.assertEqual(selected_month({"month": "12"}, today=TODAY), date(2026, 12, 1))

    def test_december_and_january_navigation(self):
        december = build_month(date(2026, 12, 1), [], today=TODAY)
        january = build_month(date(2027, 1, 1), [], today=TODAY)
        self.assertEqual(december["next_month"], date(2027, 1, 1))
        self.assertEqual(january["previous_month"], date(2026, 12, 1))

    def test_navigation_limits(self):
        self.assertIsNone(build_month(date(1900, 1, 1), [])["previous_month"])
        self.assertIsNone(build_month(date(2100, 12, 1), [])["next_month"])

    def test_grid_starts_monday_and_handles_leap_year_and_six_weeks(self):
        for first in (FIRST, date(2024, 2, 1), date(2026, 3, 1)):
            with self.subTest(first=first):
                month = build_month(first, [], today=TODAY)
                days = [day for week in month["weeks"] for day in week]
                self.assertEqual(len(days) % 7, 0)
                self.assertEqual(days[0]["date"].weekday(), 0)
                self.assertEqual(days[-1]["date"].weekday(), 6)
                self.assertEqual(sum(day["in_month"] for day in days),
                                 29 if first.month == 2 else 31)
        self.assertEqual(len(build_month(date(2026, 3, 1), [])["weeks"]), 6)

    def test_multiple_events_today_and_adjacent_days(self):
        late = event(name="Más tarde", starts_at=event()["starts_at"].replace(hour=20))
        month = build_month(FIRST, [late, event()], today=TODAY)
        days = [day for week in month["weeks"] for day in week]
        today = next(day for day in days if day["date"] == TODAY)
        self.assertTrue(today["is_today"])
        self.assertEqual([item["name"] for item in today["events"]],
                         ["Mates en el parque", "Más tarde"])
        self.assertEqual(month["event_count"], 2)
        self.assertTrue(any(not day["in_month"] for day in days))

    def test_events_from_other_month_are_not_included(self):
        other = event(starts_at=event()["starts_at"].replace(month=9))
        self.assertEqual(build_month(FIRST, [other])["event_count"], 0)

    def test_month_bounds_use_django_current_timezone(self):
        with timezone.override("America/Argentina/Buenos_Aires"):
            start, end = month_bounds(FIRST)
            self.assertEqual(start.isoformat(), "2026-10-01T00:00:00-03:00")
            self.assertEqual(end.isoformat(), "2026-11-01T00:00:00-03:00")
        with timezone.override("Asia/Tokyo"):
            self.assertEqual(month_bounds(FIRST)[0].utcoffset().total_seconds(), 9 * 3600)


class CalendarEventServiceTests(SimpleTestCase):
    def setUp(self):
        self.plans = patch.object(plan_service, "list_meetings", return_value=[]).start()
        self.groups = patch.object(calendar_service.group_service, "get_group",
                                   return_value={"id": GROUP_ID, "name": "Amigos"}).start()
        self.addCleanup(patch.stopall)

    def test_empty(self):
        self.assertEqual(calendar_service.list_events("jwt", FIRST), [])
        self.groups.assert_not_called()

    def test_only_confirmed_only_finished_and_mixed(self):
        for statuses in ([True], [False], [True, False]):
            with self.subTest(statuses=statuses):
                self.plans.return_value = [meeting(status=status) for status in statuses]
                events = calendar_service.list_events("jwt", FIRST)
                self.assertEqual([item["status_label"] for item in events],
                                 ["Confirmado" if status else "Finalizado" for status in statuses])

    def test_organizing_and_inactive_with_tentative_date_are_excluded(self):
        tentative = meeting()
        tentative["selected_proposal_id"] = None
        inactive = {**tentative, "status": False}
        self.plans.return_value = [tentative, inactive]
        self.assertEqual(calendar_service.list_events("jwt", FIRST), [])
        self.groups.assert_not_called()

    def test_foreign_or_former_group_is_excluded_even_for_plan_creator(self):
        self.plans.return_value = [meeting(), meeting(id=SECOND_PLAN, group_id=FOREIGN_GROUP)]
        self.groups.side_effect = [{"id": GROUP_ID, "name": "Amigos"}, GroupNotFound()]
        self.assertEqual([item["id"] for item in calendar_service.list_events("jwt", FIRST)],
                         [PLAN_ID])
        self.groups.assert_any_call("jwt", FOREIGN_GROUP)

    def test_no_group_and_no_definitive_date_are_excluded(self):
        self.plans.return_value = [meeting(group_id=None), meeting(scheduled_at=None)]
        self.assertEqual(calendar_service.list_events("jwt", FIRST), [])
        self.groups.assert_not_called()

    def test_membership_checked_once_per_group_including_revoked_membership(self):
        self.plans.return_value = [meeting(), meeting(id=SECOND_PLAN)]
        self.assertEqual(len(calendar_service.list_events("jwt", FIRST)), 2)
        self.groups.assert_called_once_with("jwt", GROUP_ID)
        self.groups.reset_mock()
        self.groups.side_effect = GroupNotFound()
        self.assertEqual(calendar_service.list_events("jwt", FIRST), [])
        self.groups.assert_called_once_with("jwt", GROUP_ID)

    def test_local_midnight_boundaries_and_time_order(self):
        self.plans.return_value = [
            meeting(id=SECOND_PLAN, scheduled_at="2026-11-01T02:59:00Z"),
            meeting(scheduled_at="2026-10-01T03:00:00Z"),
            meeting(scheduled_at="2026-10-01T02:59:00Z"),
            meeting(scheduled_at="2026-11-01T03:00:00Z"),
        ]
        with timezone.override("America/Argentina/Buenos_Aires"):
            events = calendar_service.list_events("jwt", FIRST)
        self.assertEqual([item["starts_at"].strftime("%d %H:%M") for item in events],
                         ["01 00:00", "31 23:59"])
        start, end = month_bounds(FIRST)
        self.plans.assert_called_once_with("jwt", start=start, end=end)

    def test_respects_active_timezone(self):
        self.plans.return_value = [meeting(scheduled_at="2026-10-26T23:00:00Z")]
        with timezone.override("Asia/Tokyo"):
            events = calendar_service.list_events("jwt", FIRST)
        self.assertEqual(events[0]["starts_at"].strftime("%d %H:%M"), "27 08:00")

    def test_naive_definitive_date_is_not_silently_interpreted(self):
        self.plans.return_value = [meeting(scheduled_at="2026-10-26T18:30:00")]
        with self.assertRaises(PlanUnavailable):
            calendar_service.list_events("jwt", FIRST)

    def test_permission_service_failure_is_not_a_false_empty_month(self):
        self.plans.return_value = [meeting()]
        self.groups.side_effect = GroupUnavailable()
        with self.assertRaises(GroupUnavailable):
            calendar_service.list_events("jwt", FIRST)


class CalendarViewTests(TestCase):
    def setUp(self):
        self.current = patch("apps.users.services.session_service.current_user",
                             return_value={"id": USER_ID, "username": "Ana"}).start()
        patch("apps.users.services.session_service.get_access_token", return_value="jwt").start()
        patch("apps.notifications.services.unread_count", return_value=0).start()
        patch("apps.users.calendar_views.timezone.localdate", return_value=TODAY).start()
        self.real_list_events = calendar_service.list_events
        self.events = patch.object(calendar_service, "list_events", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def test_requires_authentication(self):
        self.current.return_value = None
        response = self.client.get("/calendario/")
        self.assertRedirects(response, "/login/?next=%2Fcalendario%2F", fetch_redirect_response=False)
        self.events.assert_not_called()

    def test_empty_keeps_calendar_navigation_and_mobile_overview(self):
        response = self.client.get(reverse("users:calendar"))
        self.assertContains(response, "No tenés encuentros programados este mes.")
        self.assertContains(response, 'class="pb-calendar-grid"')
        self.assertContains(response, 'class="pb-calendar-overview"')
        self.assertContains(response, "Octubre 2026")
        self.assertContains(response, 'aria-current="date"')
        self.assertContains(response, 'is-outside')
        self.assertEqual(response.context["event_count"], 0)
        self.events.assert_called_once_with("jwt", FIRST)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_sidebar_calendar_active_and_plans_inactive(self):
        response = self.client.get("/calendario/")
        self.assertContains(response, '<a href="/calendario/" class="social-side-active" aria-current="page">')
        self.assertNotContains(response, '<a href="/planes/" class="social-side-active"')
        self.assertContains(response, 'users/css/personal-calendar.css')

    def test_events_link_to_existing_detail_with_time_and_states(self):
        self.events.return_value = [event(), event(id=SECOND_PLAN, name="Cena de ayer",
                                                  phase="finished", status_label="Finalizado")]
        response = self.client.get("/calendario/")
        self.assertContains(response, 'pb-calendar-event--confirmed')
        self.assertContains(response, 'pb-calendar-event--finished')
        self.assertContains(response, "15:30")
        self.assertContains(response, "Cena de ayer")
        self.assertContains(response, 'href="' + reverse("plans:detail", args=[PLAN_ID]) + '"', count=2)
        self.assertContains(response, 'href="' + reverse("plans:detail", args=[SECOND_PLAN]) + '"', count=2)
        self.assertContains(response, 'href="#calendar-day-2026-10-26"')

    def test_event_link_opens_the_existing_plan_detail(self):
        self.events.return_value = [event()]
        response = self.client.get("/calendario/")
        self.assertContains(response, 'href="/planes/' + PLAN_ID + '/"')
        with patch("apps.plans.views.plan_service.get_plan", return_value=meeting()) as get_plan, \
             patch("apps.plans.views.group_service.get_group", return_value={
                 "id": GROUP_ID, "name": "Amigos", "role": "member"}), \
             patch("apps.plans.views.proposal_service.list_proposals", return_value=[]), \
             patch("apps.plans.views.profile_service.usernames_for_ids", return_value={}):
            detail = self.client.get(reverse("plans:detail", args=[PLAN_ID]))
        self.assertEqual(detail.status_code, 200)
        self.assertTemplateUsed(detail, "plans/detail.html")
        get_plan.assert_called_once_with("jwt", UUID(PLAN_ID))

    def test_multiple_events_are_all_available(self):
        self.events.return_value = [event(name="Encuentro " + str(i)) for i in range(15)]
        response = self.client.get("/calendario/")
        self.assertEqual(response.context["event_count"], 15)
        self.assertContains(response, "Encuentro 14")
        self.assertContains(response, 'class="pb-calendar-day-events" tabindex="0"')

    def test_navigation_links_cross_year(self):
        response = self.client.get("/calendario/?year=2026&month=12")
        self.assertContains(response, 'href="?year=2027&amp;month=1"')
        self.assertContains(response, 'href="?year=2026&amp;month=11"')
        january = self.client.get("/calendario/?year=2027&month=1")
        self.assertContains(january, 'href="?year=2026&amp;month=12"')
        self.assertContains(january, 'href="/calendario/">Hoy</a>')

    def test_invalid_params_do_not_break_page(self):
        for query in ("year=abc&month=8", "year=2026&month=99", "year=-1&month=1"):
            with self.subTest(query=query):
                response = self.client.get("/calendario/?" + query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["month_date"], FIRST)

    def test_service_error_preserves_grid_and_retry_in_same_month(self):
        for error in (PlanUnavailable(), GroupUnavailable()):
            self.events.side_effect = error
            response = self.client.get("/calendario/?year=2027&month=1")
            self.assertContains(response, "No pudimos cargar tus encuentros", status_code=503)
            self.assertContains(response, 'class="pb-calendar-grid"', status_code=503)
            self.assertContains(response, 'href="?year=2027&amp;month=1"', status_code=503)
            self.assertNotContains(response, "No tenés encuentros programados", status_code=503)

    def test_expired_session_redirects_to_login(self):
        self.events.side_effect = SessionExpired()
        response = self.client.get("/calendario/")
        self.assertRedirects(response, "/login/?next=%2Fcalendario%2F", fetch_redirect_response=False)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post("/calendario/").status_code, 405)
        self.events.assert_not_called()

    def test_event_names_are_escaped(self):
        self.events.return_value = [event(name='<script>alert("x")</script>')]
        response = self.client.get("/calendario/")
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_view_filters_states_and_current_memberships_through_real_service(self):
        self.events.side_effect = self.real_list_events
        organizing = meeting(name="No mostrar organización")
        organizing["selected_proposal_id"] = None
        inactive = {**organizing, "status": False, "name": "No mostrar inactivo"}
        plans = [meeting(name="Encuentro confirmado"),
                 meeting(id=SECOND_PLAN, name="Encuentro finalizado", status=False),
                 meeting(group_id=FOREIGN_GROUP, name="No mostrar grupo ajeno"),
                 organizing, inactive]
        with patch.object(plan_service, "list_meetings", return_value=plans), \
             patch.object(calendar_service.group_service, "get_group", side_effect=[
                 {"id": GROUP_ID, "name": "Amigos"}, GroupNotFound()]):
            response = self.client.get("/calendario/")
        self.assertEqual(response.context["event_count"], 2)
        self.assertContains(response, "Encuentro confirmado")
        self.assertContains(response, "Encuentro finalizado")
        self.assertNotContains(response, "No mostrar")
