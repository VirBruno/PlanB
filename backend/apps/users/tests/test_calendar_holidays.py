"""Feriados generales y semántica visual, sin red ni cambios en los planes."""
from datetime import date
from html.parser import HTMLParser
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.users.calendar_month import build_month
from apps.users.holidays import holiday_for
from apps.users.tests.test_calendar import event, TODAY, PLAN_ID, SECOND_PLAN, USER_ID


class CalendarMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.days = {}
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        day = attributes.get("data-calendar-date")
        if day:
            self.days.setdefault(day, []).append(attributes)


class ArgentineHolidayProviderTests(SimpleTestCase):
    def test_verified_2026_observed_dates(self):
        expected = {
            (1, 1), (2, 16), (2, 17), (3, 23), (3, 24), (4, 2), (4, 3),
            (5, 1), (5, 25), (6, 15), (6, 20), (7, 9), (7, 10), (8, 17),
            (10, 12), (11, 9), (11, 23), (12, 7), (12, 8), (12, 25),
        }
        actual = set()
        for month in range(1, 13):
            days = build_month(date(2026, month, 1), [])["weeks"]
            actual.update((day["date"].month, day["date"].day)
                          for week in days for day in week if day["in_month"] and day["holiday"])
        self.assertEqual(actual, expected)

    def test_transferred_days_use_observed_date_only(self):
        for month, observed, original in ((6, 15, 17), (11, 23, 20)):
            self.assertEqual(holiday_for(date(2026, month, observed)).kind, "transferred")
            self.assertIsNone(holiday_for(date(2026, month, original)))

    def test_tourism_days_are_not_labelled_as_national_holidays(self):
        for month, day in ((3, 23), (7, 10), (12, 7)):
            holiday = holiday_for(date(2026, month, day))
            self.assertEqual(holiday.kind, "tourism")
            self.assertEqual(holiday.label, "No laborable")
            self.assertIn("turísticos", holiday.name)
        self.assertEqual(holiday_for(date(2026, 12, 25)).label, "Feriado")

    def test_excludes_limited_religious_and_territorial_days(self):
        for month, day in ((4, 1), (9, 12), (11, 10), (11, 11)):
            self.assertIsNone(holiday_for(date(2026, month, day)))
        self.assertIsNotNone(holiday_for(date(2026, 11, 9)))

    def test_year_without_data_has_no_inferred_holidays(self):
        for year in (1900, 2025, 2027, 2100):
            self.assertIsNone(holiday_for(date(year, 1, 1)))
            self.assertIsNone(holiday_for(date(year, 12, 25)))


class CalendarDaySemanticsTests(SimpleTestCase):
    def days(self, first):
        return {day["date"]: day for week in build_month(first, [])["weeks"] for day in week}

    def test_saturday_and_sunday_are_weekends_but_monday_is_not(self):
        days = self.days(date(2026, 10, 1))
        self.assertTrue(days[date(2026, 10, 3)]["is_weekend"])
        self.assertTrue(days[date(2026, 10, 4)]["is_weekend"])
        self.assertFalse(days[date(2026, 10, 5)]["is_weekend"])
        self.assertIsNone(days[date(2026, 10, 5)]["holiday"])

    def test_holiday_on_weekend_preserves_both_semantics(self):
        day = self.days(date(2026, 6, 1))[date(2026, 6, 20)]
        self.assertTrue(day["is_weekend"])
        self.assertIn("Belgrano", day["holiday"].name)

    def test_known_holiday_has_name_and_month_navigation_remains_intact(self):
        december = build_month(date(2026, 12, 1), [])
        self.assertTrue(december["has_holidays"])
        self.assertEqual(self.days(date(2026, 12, 1))[date(2026, 12, 25)]["holiday"].name, "Navidad")
        january = build_month(december["next_month"], [])
        self.assertEqual(january["previous_month"], date(2026, 12, 1))
        self.assertFalse(january["has_holidays"])
        self.assertTrue(all(day["holiday"] is None for week in january["weeks"]
                            for day in week if day["in_month"]))


class CalendarHolidayViewTests(TestCase):
    def setUp(self):
        patch("apps.users.services.session_service.current_user",
              return_value={"id": USER_ID, "username": "Ana"}).start()
        patch("apps.users.services.session_service.get_access_token", return_value="jwt").start()
        patch("apps.notifications.services.unread_count", return_value=0).start()
        patch("apps.users.calendar_views.timezone.localdate", return_value=TODAY).start()
        self.events = patch("apps.users.services.calendar_service.list_events", return_value=[]).start()
        self.addCleanup(patch.stopall)

    def page(self, year, month):
        response = self.client.get(f"/calendario/?year={year}&month={month}")
        self.assertEqual(response.status_code, 200)
        return response, CalendarMarkup(response.content.decode())

    def test_empty_days_render_weekend_classes_in_both_views(self):
        response, markup = self.page(2026, 10)
        for number, weekend in ((3, True), (4, True), (5, False)):
            elements = markup.days[f"2026-10-0{number}"]
            self.assertEqual(len(elements), 2)
            for attributes in elements:
                self.assertEqual("is-weekend" in attributes["class"].split(), weekend)
        self.assertContains(response, "No tenés encuentros programados este mes.")

    def test_holiday_names_and_labels_are_accessible_in_desktop_and_mobile(self):
        response, markup = self.page(2026, 12)
        for attributes in markup.days["2026-12-25"]:
            self.assertIn("is-holiday", attributes["class"].split())
            self.assertIn("Navidad", attributes["aria-label"])
        self.assertContains(response, 'aria-label="Feriado: Navidad"')
        self.assertContains(response, 'class="pb-calendar-holiday-list"')
        self.assertContains(response, "No laborable: Día no laborable con fines turísticos")

    def test_holiday_weekend_keeps_events_states_and_plan_links(self):
        self.events.return_value = [
            event(starts_at=event()["starts_at"].replace(month=6, day=20)),
            event(id=SECOND_PLAN, phase="finished", status_label="Finalizado",
                  starts_at=event()["starts_at"].replace(month=6, day=20)),
        ]
        response, markup = self.page(2026, 6)
        for attributes in markup.days["2026-06-20"]:
            self.assertIn("is-weekend", attributes["class"].split())
            self.assertIn("is-holiday", attributes["class"].split())
        self.assertContains(response, "pb-calendar-event--confirmed", count=2)
        self.assertContains(response, "pb-calendar-event--finished", count=2)
        for plan_id in (PLAN_ID, SECOND_PLAN):
            self.assertContains(response, 'href="' + reverse("plans:detail", args=[plan_id]) + '"', count=2)
        self.assertEqual(response.context["event_count"], 2)

    def test_navigation_uses_holidays_for_the_requested_month_and_year(self):
        october, markup = self.page(2026, 10)
        self.assertIn("is-holiday", markup.days["2026-10-12"][0]["class"].split())
        self.assertContains(october, 'href="?year=2026&amp;month=11"')
        november, markup = self.page(2026, 11)
        self.assertIn("is-holiday", markup.days["2026-11-23"][0]["class"].split())
        self.assertNotIn("is-holiday", markup.days["2026-11-20"][0]["class"].split())
        self.assertContains(november, "Soberanía Nacional")
        january, markup = self.page(2027, 1)
        self.assertNotIn("is-holiday", markup.days["2027-01-01"][0]["class"].split())
        self.assertNotContains(january, 'class="pb-calendar-holiday-list"')
        self.assertContains(january, 'class="pb-calendar-grid"')
        self.assertContains(january, 'href="?year=2026&amp;month=12"')
