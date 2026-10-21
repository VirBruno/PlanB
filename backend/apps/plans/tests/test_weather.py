"""Clima: pruebas offline, sin consultar Open-Meteo ni Supabase."""
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import httpx
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import weather_service
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID

ARG_TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def proposal_for_weather(days=2):
    future = date(2026, 10, 21) + timedelta(days=days)
    return {
        "id": 47, "created_by": USER_ID, "latitude": -34.6, "longitude": -58.38,
        "date_pick": datetime(future.year, future.month, future.day, 16, tzinfo=ARG_TZ),
    }


def fake_response():
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = {"daily": {
        "time": ["2026-10-23"], "weather_code": [2],
        "temperature_2m_max": [24.6], "temperature_2m_min": [13.4],
        "precipitation_probability_max": [35], "wind_speed_10m_max": [19.9],
    }}
    return result


class WeatherServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.clock = patch("apps.plans.services.weather_service.timezone.localdate", return_value=date(2026, 10, 21))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(cache.clear)

    @patch("apps.plans.services.weather_service.httpx.get")
    def test_single_date_forecast_and_caching(self, get):
        get.return_value = fake_response()
        item = proposal_for_weather()
        result = weather_service.forecast_for_proposal(item)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["date"], "2026-10-23")
        self.assertEqual((result["temperature_min"], result["temperature_max"]), (13, 25))
        self.assertEqual(result["rain_probability"], 35)
        self.assertEqual(result["condition"], "Parcialmente nublado")
        self.assertEqual(weather_service.forecast_for_proposal(item), result)
        get.assert_called_once()
        url, = get.call_args.args
        self.assertEqual(url, weather_service.FORECAST_URL)
        self.assertEqual(get.call_args.kwargs["params"]["start_date"], "2026-10-23")
        self.assertEqual(get.call_args.kwargs["params"]["end_date"], "2026-10-23")

    @patch("apps.plans.services.weather_service.httpx.get")
    def test_unavailable_dates_do_not_contact_external_provider(self, get):
        self.assertEqual(weather_service.forecast_for_proposal(proposal_for_weather(-1))["status"], "past")
        self.assertEqual(weather_service.forecast_for_proposal(proposal_for_weather(16))["status"], "too_early")
        self.assertEqual(weather_service.forecast_for_proposal({"date_pick": None})["status"], "missing_date")
        get.assert_not_called()

    @patch("apps.plans.services.weather_service.httpx.get")
    def test_provider_timeout_produces_controlled_exception(self, get):
        get.side_effect = httpx.TimeoutException("offline")
        with self.assertRaises(weather_service.WeatherUnavailable):
            weather_service.forecast_for_proposal(proposal_for_weather())

    @patch("apps.plans.services.weather_service.httpx.get")
    def test_incomplete_forecast_is_not_cached(self, get):
        response = fake_response()
        response.json.return_value["daily"]["time"] = ["2026-10-22"]
        get.return_value = response
        with self.assertRaises(weather_service.WeatherUnavailable):
            weather_service.forecast_for_proposal(proposal_for_weather())
        self.assertIsNone(cache.get("planb:weather:v1:-34.600:-58.380:2026-10-23"))


class ProposalWeatherViewTests(TestCase):
    def setUp(self):
        self.current = patch("apps.users.services.session_service.current_user", return_value={"id": USER_ID, "username": "Ana"})
        self.token = patch("apps.users.services.session_service.get_access_token", return_value="user-jwt")
        self.current.start()
        self.token.start()
        self.addCleanup(self.current.stop)
        self.addCleanup(self.token.stop)
        self.path = f"/planes/{PLAN_ID}/propuestas/47/clima/"
        self.plan = patch("apps.plans.views.plan_service.get_plan", return_value={"id": PLAN_ID, "group_id": GROUP_ID})
        self.group = patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "role": "member"})
        self.items = patch("apps.plans.views.proposal_service.list_proposals", return_value=[proposal_for_weather()])
        self.plan_mock = self.plan.start()
        self.group_mock = self.group.start()
        self.items_mock = self.items.start()
        self.addCleanup(self.plan.stop)
        self.addCleanup(self.group.stop)
        self.addCleanup(self.items.stop)

    @patch("apps.plans.views.weather_service.forecast_for_proposal", return_value={"status": "too_early", "message": "Faltan días"})
    def test_member_may_consult_own_group_proposal(self, forecast):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "too_early")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        forecast.assert_called_once()

    @patch("apps.plans.views.weather_service.forecast_for_proposal")
    def test_cannot_ask_weather_for_other_proposal(self, forecast):
        response = self.client.get(f"/planes/{PLAN_ID}/propuestas/55/clima/")
        self.assertEqual(response.status_code, 404)
        forecast.assert_not_called()

    @patch("apps.plans.views.weather_service.forecast_for_proposal")
    def test_not_member_cannot_access_weather(self, forecast):
        self.group_mock.side_effect = GroupNotFound()
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 404)
        forecast.assert_not_called()

    @patch("apps.plans.views.weather_service.forecast_for_proposal", side_effect=weather_service.WeatherUnavailable())
    def test_provider_error_does_not_break_plan(self, forecast):
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.path).status_code, 405)
