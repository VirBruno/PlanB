"""Comparador meteorológico de planes: pruebas aisladas de red y Supabase."""
from datetime import date, timedelta
from unittest.mock import Mock, patch

import httpx
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import plan_weather_service as service
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row


def fake_response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    return result


def city_record(admin1="Buenos Aires", city_id=3433955):
    return {"id": city_id, "name": "Mar del Plata", "country_code": "AR", "admin1": admin1,
            "latitude": -38.0, "longitude": -57.56}


def forecast_data():
    today = date(2026, 10, 21)
    return {"daily": {
        "time": [(today + timedelta(days=i)).isoformat() for i in range(16)],
        "weather_code": [1] * 16,
        "temperature_2m_min": [13.4] * 16,
        "temperature_2m_max": [24.5] * 16,
        "precipitation_probability_max": [20] * 16,
        "wind_speed_10m_max": [14.5] * 16,
    }}


class CityWeatherServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_24_argentine_jurisdictions(self):
        self.assertEqual(len(service.PROVINCES), 24)
        self.assertIn("caba", service.PROVINCES)

    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_cities_reject_outside_province_and_cache_results(self, get):
        get.return_value = fake_response({"results": [
            city_record(), city_record("Córdoba", 999),
            dict(city_record(), id=555, country_code="UY"),
        ]})
        cities = service.search_cities("buenos-aires", "Mar del Plata")
        self.assertEqual(len(cities), 1)
        self.assertEqual(cities[0]["name"], "Mar del Plata")
        self.assertEqual(cities, service.search_cities("buenos-aires", "Mar del Plata"))
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["params"]["countryCode"], "AR")

    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_multiword_search_has_memcached_safe_key(self, get):
        get.return_value = fake_response({"results": [city_record()]})
        with patch.object(service.cache, "get", wraps=service.cache.get) as cached_get:
            result = service.search_cities("buenos-aires", "Mar del Plata")
            key = cached_get.call_args.args[0]
        self.assertEqual(result[0]["name"], "Mar del Plata")
        self.assertNotIn(" ", key)
        self.assertLess(len(key), 250)
        self.assertTrue(key.isascii())
        self.assertEqual(result, service.search_cities("buenos-aires", "MAR DEL PLATA"))
        get.assert_called_once()

    def test_caba_distinct_from_province(self):
        self.assertFalse(service._matches_province("buenos-aires", "Buenos Aires F.D."))
        self.assertTrue(service._matches_province("caba", "Buenos Aires F.D."))

    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_rejects_untrusted_location_search(self, get):
        for province, term in (
            ("xxx", "Rosario"), ("santa-fe", "A"),
            ("caba", "AB\nC"), ("caba", "AB\n"),
            ("caba", "\nABC"), ("buenos-aires", "Mar\tdel Plata"),
        ):
            with self.assertRaises(ValueError):
                service.search_cities(province, term)
        get.assert_not_called()

    @patch("apps.plans.services.plan_weather_service.timezone.localdate", return_value=date(2026, 10, 21))
    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_validated_city_has_16_forecast_days_and_cache(self, get, today):
        get.side_effect = [fake_response(city_record()), fake_response(forecast_data())]
        result = service.forecast_for_city("buenos-aires", 3433955)
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["days"]), 16)
        self.assertEqual(result["days"][0]["date"], "2026-10-21")
        self.assertEqual(result["days"][-1]["date"], "2026-11-05")
        self.assertEqual(result["days"][0]["temperature_max"], 24)
        self.assertEqual(result, service.forecast_for_city("buenos-aires", 3433955))
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"]["forecast_days"], 16)

    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_city_id_must_match_province(self, get):
        get.return_value = fake_response(city_record(admin1="Córdoba"))
        with self.assertRaises(ValueError):
            service.forecast_for_city("buenos-aires", 3433955)
        get.assert_called_once()

    @patch("apps.plans.services.plan_weather_service.timezone.localdate", return_value=date(2026, 10, 21))
    @patch("apps.plans.services.plan_weather_service.httpx.get")
    def test_incomplete_forecast_is_not_cached(self, get, today):
        invalid = forecast_data()
        invalid["daily"]["time"][5] = "2027-01-01"
        get.side_effect = [fake_response(city_record()), fake_response(invalid)]
        with self.assertRaises(service.WeatherUnavailable):
            service.forecast_for_city("buenos-aires", 3433955)
        self.assertIsNone(cache.get("planb:city-forecast:v2:3433955:2026-10-21"))

    @patch("apps.plans.services.plan_weather_service.httpx.get", side_effect=httpx.TimeoutException("sin red"))
    def test_provider_failure_is_controlled(self, get):
        with self.assertRaises(service.WeatherUnavailable):
            service.search_cities("buenos-aires", "La Plata")


class PlanWeatherViewTests(TestCase):
    def setUp(self):
        self.current = patch("apps.users.services.session_service.current_user", return_value={"id": USER_ID, "username": "Ana"})
        self.token = patch("apps.users.services.session_service.get_access_token", return_value="user-jwt")
        self.plan = patch("apps.plans.views.plan_service.get_plan", return_value=plan_row())
        self.group = patch("apps.plans.views.group_service.get_group", return_value={"id": GROUP_ID, "role": "member"})
        self.current_mock = self.current.start()
        self.token.start()
        self.plan_mock = self.plan.start()
        self.group_mock = self.group.start()
        for p in (self.current, self.token, self.plan, self.group):
            self.addCleanup(p.stop)
        self.cities_url = f"/planes/{PLAN_ID}/clima/localidades/"
        self.forecast_url = f"/planes/{PLAN_ID}/clima/pronostico/"

    @patch("apps.plans.views.plan_weather_service.search_cities", return_value=[{"id": 20, "name": "La Plata", "province": "Buenos Aires"}])
    def test_group_member_can_search_cities(self, search):
        response = self.client.get(self.cities_url, {"provincia": "buenos-aires", "q": "La Plata"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cities"][0]["name"], "La Plata")
        self.assertIn("no-store", response["Cache-Control"])
        search.assert_called_once()

    @patch("apps.plans.views.plan_weather_service.forecast_for_city", return_value={"status": "available", "days": []})
    def test_member_can_get_forecast(self, forecast):
        response = self.client.get(self.forecast_url, {"provincia": "buenos-aires", "localidad_id": "20"})
        self.assertEqual(response.status_code, 200)
        forecast.assert_called_once_with("buenos-aires", 20)

    @patch("apps.plans.views.plan_weather_service.search_cities")
    def test_user_outside_group_cannot_search(self, search):
        self.group_mock.side_effect = GroupNotFound()
        self.assertEqual(self.client.get(self.cities_url, {"provincia": "chubut", "q": "Trelew"}).status_code, 404)
        search.assert_not_called()

    @patch("apps.plans.views.plan_weather_service.forecast_for_city")
    def test_invalid_city_does_not_make_provider_call(self, forecast):
        response = self.client.get(self.forecast_url, {"provincia": "buenos-aires", "localidad_id": "oops"})
        self.assertEqual(response.status_code, 400)
        forecast.assert_not_called()

    @patch("apps.plans.views.plan_weather_service.forecast_for_city", side_effect=service.WeatherUnavailable())
    def test_provider_outage_is_safe(self, forecast):
        response = self.client.get(self.forecast_url, {"provincia": "buenos-aires", "localidad_id": "20"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")

    def test_post_disallowed(self):
        self.assertEqual(self.client.post(self.cities_url).status_code, 405)
        self.assertEqual(self.client.post(self.forecast_url).status_code, 405)

    @patch("apps.plans.views.proposal_service.list_proposals", return_value=[])
    @patch("apps.plans.views.profile_service.usernames_for_ids", return_value={})
    def test_comparator_renders_even_without_proposals(self, names, proposals):
        result = self.client.get(f"/planes/{PLAN_ID}/")
        self.assertContains(result, 'data-plan-weather')
        self.assertContains(result, 'data-weather-province')
        self.assertContains(result, 'data-weather-cities')
