import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from apps.plans.services import ideal_ideas_service as service
from apps.plans.services.place_types import RecommendationError


IDEAS = [
    {"title": "Merienda con opciones bajo techo",
     "description": "Combina una charla tranquila con una alternativa protegida si llueve.",
     "place_intent": "cafe"},
    {"title": "Paseo breve y café",
     "description": "Permite disfrutar el aire libre si acompaña el clima y continuar bajo techo.",
     "place_intent": "park"},
]


def gemini_response(output, *, status=200):
    return httpx.Response(status, json={"candidates": [{
        "finishReason": "STOP",
        "content": {"role": "model", "parts": [{
            "text": json.dumps(output, ensure_ascii=False),
        }]},
    }]}, request=httpx.Request("POST", service.API_BASE + "/gemini-test-flash:generateContent"))


@override_settings(GEMINI_API_KEY="fake-gemini-test-key", GEMINI_MODEL="gemini-test-flash")
class IdealIdeasServiceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        post = patch.object(
            service.httpx, "post", return_value=gemini_response({"ideas": IDEAS}),
        )
        self.post = post.start()
        self.addCleanup(post.stop)

    def tearDown(self):
        cache.clear()

    def proposals(self):
        return [
            {
                "id": 47, "tittle": "Merienda al aire libre",
                "description": "Tomar algo en una plaza", "type": "juntada",
                "date_pick": datetime(2026, 11, 20, 15, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
                "budget_min": Decimal("100"), "budget_max": Decimal("200"),
                "latitude": -34.6, "longitude": -58.4, "created_by": "private-user",
                "email": "private@example.com", "token": "private-jwt",
            },
            {
                "id": 48, "tittle": "Café de especialidad",
                "description": "Charlar en un lugar tranquilo", "type": "salida",
                "date_pick": datetime(2026, 11, 21, 17, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
                "budget_min": Decimal("200"), "budget_max": Decimal("400"),
                "latitude": -34.7, "longitude": -58.3,
            },
        ]

    def test_suggestions_use_both_proposals_and_forecasts_then_cache_result(self):
        proposals = self.proposals()
        weather = [
            {"title": "Merienda al aire libre", "weather": {
                "status": "available", "date": "2026-11-20", "condition": "Lluvia",
                "temperature_min": 15, "temperature_max": 20,
                "rain_probability": 80, "wind_max": 24,
            }},
            {"title": "Café de especialidad", "weather": {
                "status": "too_early",
            }},
        ]

        with patch.object(service.cache, "set", wraps=service.cache.set) as cache_set:
            result = service.suggest_ideas(proposals, weather)
        self.assertEqual(result, IDEAS)
        self.assertIsNone(cache_set.call_args.kwargs["timeout"])
        args, kwargs = self.post.call_args
        self.assertEqual(args, (service.API_BASE + "/gemini-test-flash:generateContent",))
        self.assertEqual(kwargs["headers"], {"x-goog-api-key": "fake-gemini-test-key"})
        self.assertFalse(kwargs["follow_redirects"])
        self.assertEqual(kwargs["json"]["generationConfig"]["responseJsonSchema"], service.IDEAS_SCHEMA)
        data = json.loads(kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(len(data["proposals"]), 2)
        self.assertEqual(data["proposals"][0]["title"], "Merienda al aire libre")
        self.assertEqual(data["proposals"][1]["title"], "Café de especialidad")
        self.assertEqual(data["proposals"][0]["weather"]["rain_probability"], 80)
        self.assertEqual(data["proposals"][1]["weather"]["status"], "too_early")
        serialized = json.dumps(kwargs["json"])
        for private_value in ("private-user", "private@example.com", "private-jwt", "-34.6", "-58.4"):
            self.assertNotIn(private_value, serialized)

        self.assertEqual(service.suggest_ideas(proposals, weather), IDEAS)
        self.post.assert_called_once()

    def test_recommended_place_is_cached_and_reports_area_membership(self):
        candidate = {
            "provider": "geoapify", "provider_place_id": "place-1", "name": "Café de prueba",
            "categories": ["catering.cafe"], "address": "Buenos Aires",
            "latitude": -34.599, "longitude": -58.4,
            "website": None, "opening_hours": None, "media": None,
        }
        with patch.object(service.GeoapifyClient, "search", return_value=[candidate]) as search:
            place = service.recommend_place(IDEAS, [-34.6, -58.4])
            self.assertEqual(service.recommend_place(IDEAS, [-34.6, -58.4]), place)

        self.assertEqual(place["status"], "available")
        self.assertTrue(place["is_in_area"])
        self.assertLessEqual(place["distance_meters"], 200)
        self.assertIn("google.com/maps/search/", place["google_maps_url"])
        search.assert_called_once()
        criteria = search.call_args.args[0]
        self.assertIn("catering.cafe", criteria.categories)
        self.assertEqual(criteria.radius_meters, 5000)

    def test_recommended_place_can_be_outside_ideal_area(self):
        candidate = {
            "provider": "geoapify", "provider_place_id": "place-2", "name": "Café lejano",
            "categories": ["catering.cafe"], "address": "Buenos Aires",
            "latitude": -34.597, "longitude": -58.4,
            "website": None, "opening_hours": None, "media": None,
        }
        with patch.object(service.GeoapifyClient, "search", return_value=[candidate]):
            place = service.recommend_place(IDEAS, [-34.6, -58.4])

        self.assertEqual(place["status"], "available")
        self.assertFalse(place["is_in_area"])
        self.assertGreater(place["distance_meters"], 200)

    def test_rain_marks_only_home_proposals_with_rain_forecasts(self):
        proposals = self.proposals()
        proposals[0]["tittle"] = "Merienda en casa"
        proposals[0]["description"] = "Una opción tranquila"
        proposals[1]["description"] = "Nos juntamos en el hogar"
        weather = [
            {"weather": {"status": "available", "rain_probability": 80}},
            {"weather": {"status": "available", "rain_probability": 0, "condition": "Despejado"}},
        ]

        result = service.rainy_home_proposals(proposals, weather)

        self.assertEqual(result, [{
            "title": "Merienda en casa", "latitude": -34.6, "longitude": -58.4,
        }])

    @patch.object(service.weather_service, "forecast_for_proposal")
    def test_forecast_failure_is_reported_per_proposal(self, forecast):
        forecast.side_effect = [
            {"status": "available", "condition": "Despejado", "temperature_min": 18,
             "temperature_max": 26, "rain_probability": 0, "wind_max": 10},
            service.weather_service.WeatherUnavailable(),
        ]

        result = service.weather_for_proposals(self.proposals())

        self.assertEqual(result[0]["weather"]["status"], "available")
        self.assertEqual(result[0]["weather"]["condition"], "Despejado")
        self.assertEqual(result[1]["weather"], {"status": "unavailable"})
        self.assertEqual(forecast.call_count, 2)

    def test_invalid_ideas_are_rejected(self):
        client = service.GeminiIdealIdeasClient()
        self.post.return_value = gemini_response({
            "ideas": [
                {**IDEAS[0], "place_intent": "untrusted"},
                IDEAS[1],
            ],
        })

        with self.assertRaises(RecommendationError) as caught:
            client.generate({"proposals": []})

        self.assertEqual(caught.exception.code, "invalid_response")

    @override_settings(GEMINI_API_KEY="")
    def test_missing_api_key_does_not_issue_request(self):
        with self.assertRaises(RecommendationError) as caught:
            service.GeminiIdealIdeasClient().generate({"proposals": []})

        self.assertEqual(caught.exception.code, "missing_api_key")
        self.post.assert_not_called()
