"""Recomendaciones offline: proveedor simulado, SDK real sobre MockTransport."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from django.test import Client, SimpleTestCase, TestCase, override_settings

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import (
    gemini_intent_service, geoapify_service, place_intent_service, plan_recommendation_service as service,
    recommendation_store as store,
)
from apps.plans.services.exceptions import PlanNotFound
from apps.plans.services.place_ranking_service import (
    distance_meters, google_maps_url, google_search_url, rank_candidates,
)
from apps.plans.services.place_types import Location, PlaceSearch, RecommendationError
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.users.services import clients
from apps.users.services.exceptions import SessionExpired

BASE = Location(-34.6, -58.38)
CONFIRMED = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
TICKET = "2571fed2-5b8d-4aa4-8f03-9abf9caeb829"


def plan(**changes):
    return plan_row(**{"selected_proposal_id": 47, "confirmed_at": CONFIRMED, **changes})


def feature(identifier="poi1", **changes):
    return {"type": "Feature", "properties": {
        "place_id": identifier, "name": "Café de prueba", "lat": BASE.latitude,
        "lon": BASE.longitude, "categories": ["catering.cafe"], **changes,
    }}


def candidate(identifier="poi1", **changes):
    return {**geoapify_service.GeoapifyClient._candidate(feature(identifier)), **changes}


def row(**changes):
    return {"plan_id": PLAN_ID, "proposal_id": 47, "confirmed_at": CONFIRMED.isoformat(),
            "status": "ready", "error_code": None, "items": [], **changes}


@override_settings(GEOAPIFY_API_KEY="fake-geoapify-test-key")
class GeoapifyTests(SimpleTestCase):
    def setUp(self):
        self.http = patch.object(geoapify_service.httpx, "get")
        self.get = self.http.start()
        self.addCleanup(self.http.stop)
        self.provider = geoapify_service.GeoapifyClient()
        self.criteria = PlaceSearch(("catering.cafe",), BASE)

    def response(self, features, status=200):
        self.get.return_value = httpx.Response(status, json={
            "type": "FeatureCollection", "features": features,
        }, request=httpx.Request("GET", geoapify_service.PLACES_URL))

    def test_fixed_endpoint_timeout_filter_and_bounded_candidates(self):
        self.response([feature()])
        result = self.provider.search(self.criteria)
        args, kwargs = self.get.call_args
        self.assertEqual(args, (geoapify_service.PLACES_URL,))
        self.assertEqual(kwargs["params"]["limit"], 20)
        self.assertEqual(kwargs["params"]["filter"], "circle:-58.38,-34.6,5000")
        self.assertEqual(kwargs["params"]["bias"], "circle:-58.38,-34.6,600")
        self.assertEqual(kwargs["timeout"].connect, 2)
        self.assertEqual(kwargs["timeout"].read, 6)
        self.assertFalse(kwargs["follow_redirects"])
        self.assertNotIn("fake-geoapify-test-key", str(result))
        self.get.assert_called_once()

    @override_settings(GEOAPIFY_API_KEY="")
    def test_missing_key_is_controlled_and_does_not_send_request(self):
        with self.assertRaises(RecommendationError) as caught:
            self.provider.search(self.criteria)
        self.assertEqual(caught.exception.code, "missing_api_key")
        self.get.assert_not_called()

    def test_timeout_has_safe_code_and_no_chained_credentials(self):
        self.get.side_effect = httpx.ReadTimeout("sensitive upstream URL")
        with self.assertRaises(RecommendationError) as caught:
            self.provider.search(self.criteria)
        self.assertEqual(str(caught.exception), "timeout")
        self.assertTrue(caught.exception.__suppress_context__)

    def test_http_errors_including_quota_and_redirects(self):
        for status in (302, 401, 429, 500):
            with self.subTest(status=status):
                self.response([], status)
                with self.assertRaises(RecommendationError) as caught:
                    self.provider.search(self.criteria)
                self.assertEqual(caught.exception.code, "http_error")

    def test_connection_error_is_controlled(self):
        self.get.side_effect = httpx.ConnectError("unavailable")
        with self.assertRaises(RecommendationError) as caught:
            self.provider.search(self.criteria)
        self.assertEqual(caught.exception.code, "http_error")

    def test_invalid_envelope_or_json(self):
        for payload in (None, [], {}, {"type": "FeatureCollection", "features": {}},
                        {"features": []}):
            self.get.return_value = httpx.Response(200, json=payload)
            self.get.return_value.request = httpx.Request("GET", geoapify_service.PLACES_URL)
            with self.subTest(payload=payload), self.assertRaises(RecommendationError) as caught:
                self.provider.search(self.criteria)
            self.assertEqual(caught.exception.code, "invalid_response")
        self.get.return_value = httpx.Response(200, text="not-json", request=httpx.Request(
            "GET", geoapify_service.PLACES_URL))
        with self.assertRaises(RecommendationError):
            self.provider.search(self.criteria)

    def test_empty_result_is_success(self):
        self.response([])
        self.assertEqual(self.provider.search(self.criteria), [])

    def test_preferred_bias_respects_smaller_requested_radius_without_extra_calls(self):
        self.response([feature()])
        self.provider.search(PlaceSearch(("catering.cafe",), BASE, radius_meters=300))
        params = self.get.call_args.kwargs["params"]
        self.assertEqual(params["filter"], "circle:-58.38,-34.6,300")
        self.assertEqual(params["bias"], "circle:-58.38,-34.6,300")
        self.get.assert_called_once()

    def test_optional_fields_absent_are_null_and_not_invented(self):
        self.response([feature()])
        item = self.provider.search(self.criteria)[0]
        for field in ("website", "opening_hours", "media", "address"):
            self.assertIsNone(item[field])
        for field in ("rating", "reviews", "price", "open_now", "photo"):
            self.assertNotIn(field, item)

    def test_available_optional_fields_and_raw_payload_not_exposed(self):
        self.response([feature(formatted="Calle 123", datasource={"raw": {
            "website": "https://cafe.example/", "opening_hours": "Mo-Fr 08:00-18:00",
            "irrelevant": "private",
        }})])
        item = self.provider.search(self.criteria)[0]
        self.assertEqual(item["address"], "Calle 123")
        self.assertEqual(item["website"], "https://cafe.example/")
        self.assertEqual(item["opening_hours"], "Mo-Fr 08:00-18:00")
        self.assertNotIn("datasource", item)
        self.assertNotIn("private", str(item))

    def test_unsafe_website_is_not_retained(self):
        for url in ("javascript:alert(1)", "file:///secret", "https://u:pw@example.com/", "http://["):
            self.response([feature(website=url)])
            self.assertIsNone(self.provider.search(self.criteria)[0]["website"])

    def test_malformed_candidates_skipped_but_all_malformed_is_failure(self):
        malformed = [None, {}, {"properties": None}, feature(lat=True), feature(lon=181),
                     feature(lat="NaN"), feature(name=None), feature(place_id=None),
                     feature(categories="catering.cafe")]
        self.response([*malformed, feature()])
        self.assertEqual(len(self.provider.search(self.criteria)), 1)
        self.response(malformed)
        with self.assertRaises(RecommendationError) as caught:
            self.provider.search(self.criteria)
        self.assertEqual(caught.exception.code, "invalid_response")

    def test_geocode_fixed_endpoint_with_limit_one(self):
        self.response([feature()])
        self.assertEqual(self.provider.geocode("La Plata, Argentina"), BASE)
        self.assertEqual(self.get.call_args.args[0], geoapify_service.GEOCODING_URL)
        self.assertEqual(self.get.call_args.kwargs["params"]["limit"], 1)

    def test_geocode_empty_and_malformed(self):
        for features, code in (([], "location_not_found"), ([{}], "invalid_response")):
            self.response(features)
            with self.assertRaises(RecommendationError) as caught:
                self.provider.geocode("La Plata")
            self.assertEqual(caught.exception.code, code)


class IntentAndRankingTests(SimpleTestCase):
    def test_controlled_mapping_for_multiple_intents(self):
        result = place_intent_service.validate_intents(
            {"place_intents": ["bar", "nightclub"]}, model="gemini-3.8-flash")
        self.assertEqual(result.categories, ("catering.bar", "catering.pub", "adult.nightclub"))

    def test_unsupported_is_not_mapped_to_default_categories(self):
        with self.assertRaises(RecommendationError) as caught:
            place_intent_service.validate_intents(
                {"place_intents": ["unsupported"]}, model="gemini-3.8-flash")
        self.assertEqual(caught.exception.code, "unsupported_intent")

    @patch.object(geoapify_service.GeoapifyClient, "geocode")
    def test_existing_coordinates_never_geocoded(self, geocode):
        result = place_intent_service.resolve_location({
            "latitude": BASE.latitude, "longitude": BASE.longitude, "location_text": "ignored",
        }, geoapify_service.GeoapifyClient())
        self.assertEqual(result, BASE)
        geocode.assert_not_called()

    @patch.object(geoapify_service.GeoapifyClient, "geocode", return_value=BASE)
    def test_textual_location_geocoded_only_when_coordinates_invalid(self, geocode):
        self.assertEqual(place_intent_service.resolve_location({
            "latitude": None, "longitude": None, "location_text": "La Plata",
        }, geoapify_service.GeoapifyClient()), BASE)
        geocode.assert_called_once_with("La Plata")

    def test_title_is_not_used_as_an_address(self):
        with self.assertRaises(RecommendationError) as caught:
            place_intent_service.resolve_location({"tittle": "Cena en La Plata"}, None)
        self.assertEqual(caught.exception.code, "missing_location")

    def test_structured_search_rejects_unbounded_or_invalid_inputs(self):
        cases = ({"candidate_limit": 21}, {"radius_meters": -1}, {"radius_meters": True},
                 {"categories": ()}, {"categories": ("https://evil.invalid",)})
        for changes in cases:
            with self.assertRaises(ValueError):
                PlaceSearch(**{"categories": ("catering.cafe",), "location": BASE, **changes})

    def test_invalid_coordinates_rejected(self):
        for lat, lon in ((True, 1), (91, 1), (1, float("inf")), ("1", 1), (None, None)):
            with self.assertRaises(ValueError):
                Location(lat, lon)

    def test_distance_is_geodesic_and_zero_at_base(self):
        self.assertEqual(distance_meters(BASE, BASE.latitude, BASE.longitude), 0)
        self.assertAlmostEqual(distance_meters(Location(0, 0), 0, 1), 111195.08, places=1)

    def test_ranking_category_then_information_then_distance_and_separation(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        items = [candidate("far", latitude=-34.61), candidate("near"),
                 candidate("rich", address="Calle 1"), candidate("child", categories=["catering.cafe.coffee"]),
                 candidate("wrong", categories=["commercial.supermarket"]),
                 candidate("outside", latitude=-35.6)]
        self.assertEqual([r["provider_place_id"] for r in rank_candidates(items, criteria)],
                         ["rich", "far", "near", "child"])

    def place_at(self, identifier, meters, **changes):
        return candidate(identifier, latitude=BASE.latitude + meters / 111195.08, **changes)

    def test_more_complete_data_precedes_nearest_place_within_preferred_area(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at("near", 46), self.place_at("documented", 420, address="Calle 1",
                                                           website="https://cafe.example/", opening_hours="Mo-Fr")]
        result = rank_candidates(places, criteria)
        self.assertEqual([p["provider_place_id"] for p in result], ["documented", "near"])
        self.assertNotIn("quality_score", result[0])
        self.assertNotIn("rating", result[0])

    def test_exact_category_precedes_more_complete_descendant_match(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at("exact", 500), self.place_at("child", 100,
                  categories=["catering.cafe.coffee"], address="Calle 1", website="https://example.com/", opening_hours="Mo-Fr")]
        self.assertEqual(rank_candidates(places, criteria)[0]["provider_place_id"], "exact")

    def test_specific_category_is_objective_information_within_same_compatibility(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at("generic", 50), self.place_at("specific", 400,
                  categories=["catering.cafe", "catering.cafe.coffee"])]
        self.assertEqual(rank_candidates(places, criteria)[0]["provider_place_id"], "specific")

    def test_equal_information_explores_area_without_excluding_nearby_places(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at(str(m), m) for m in (46, 85, 151, 152, 159, 340, 550)]
        result = rank_candidates(places, criteria)
        self.assertEqual([p["provider_place_id"] for p in result[:3]], ["46", "340", "550"])
        self.assertEqual(len(result), 5)
        self.assertTrue(all(p["distance_meters"] <= 600 for p in result))
        self.assertEqual(result, rank_candidates(list(reversed(places)), criteria))

    def test_close_cluster_is_retained_if_no_spread_alternatives_are_available(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at(str(m), m) for m in (46, 85, 151, 152, 159)]
        self.assertEqual(len(rank_candidates(places, criteria)), 5)

    def test_preferred_area_does_not_select_remote_places_only_for_more_fields(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at(str(m), m) for m in (50, 200, 350, 450, 550)]
        places.append(self.place_at("remote", 1500, address="Calle 1", website="https://example.com/", opening_hours="Mo-Fr"))
        result = rank_candidates(places, criteria)
        self.assertNotIn("remote", [p["provider_place_id"] for p in result])

    def test_sparse_area_expands_only_as_far_as_needed_with_existing_candidates(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at(str(m), m) for m in (50, 400, 700, 900, 1200, 3000, 5500)]
        result = rank_candidates(places, criteria)
        self.assertEqual({p["provider_place_id"] for p in result}, {"50", "400", "700", "900", "1200"})

    def test_too_few_candidates_and_custom_radius_do_not_fabricate_or_exceed_limits(self):
        criteria = PlaceSearch(("catering.cafe",), BASE, radius_meters=300)
        places = [self.place_at(str(m), m) for m in (50, 250, 400)]
        self.assertEqual({p["provider_place_id"] for p in rank_candidates(places, criteria)}, {"50", "250"})
        self.assertEqual(rank_candidates([], criteria), [])

    def test_duplicate_ids_do_not_hide_sparse_area_fallback(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        places = [self.place_at("near", 50)] * 5 + [self.place_at("other", 800)]
        result = rank_candidates(places, criteria)
        self.assertEqual({p["provider_place_id"] for p in result}, {"near", "other"})

    def test_order_is_stable_deduplicated_and_at_most_five(self):
        criteria = PlaceSearch(("catering.cafe",), BASE)
        items = [candidate(str(i)) for i in reversed(range(10))] + [candidate("0")]
        result = rank_candidates(items, criteria)
        self.assertEqual(result, rank_candidates(list(reversed(items)), criteria))
        self.assertEqual([r["provider_place_id"] for r in result], ["0", "1", "2", "3", "4"])
        self.assertEqual([r["rank"] for r in result], [1, 2, 3, 4, 5])

    def test_google_maps_uses_name_and_coordinates_without_address(self):
        url = urlsplit(google_maps_url(candidate()))
        self.assertEqual(url.netloc, "www.google.com")
        self.assertEqual(parse_qs(url.query), {
            "api": ["1"], "query": ["Café de prueba, -34.6,-58.38"],
        })

    def test_google_links_prefer_name_and_address(self):
        item = candidate(name="El Porteño", address="City Bell, Buenos Aires")
        expected = "El Porteño, City Bell, Buenos Aires"
        self.assertEqual(parse_qs(urlsplit(google_maps_url(item)).query), {
            "api": ["1"], "query": [expected],
        })
        search = urlsplit(google_search_url(item))
        self.assertEqual((search.scheme, search.netloc, search.path),
                         ("https", "www.google.com", "/search"))
        self.assertEqual(parse_qs(search.query), {"q": [expected]})

    def test_google_links_fallbacks_for_missing_or_blank_fields(self):
        for name, address, expected in (
            ("Café", None, "Café, -34.6,-58.38"),
            ("Café", "", "Café, -34.6,-58.38"),
            ("  Café  ", " \t ", "Café, -34.6,-58.38"),
            (None, "Calle 123", "-34.6,-58.38"),
            ("", "Calle 123", "-34.6,-58.38"),
            ("  ", None, "-34.6,-58.38"),
            (None, None, "-34.6,-58.38"),
        ):
            with self.subTest(name=name, address=address):
                item = candidate(name=name, address=address)
                self.assertEqual(parse_qs(urlsplit(google_maps_url(item)).query), {
                    "api": ["1"], "query": [expected],
                })
                self.assertEqual(parse_qs(urlsplit(google_search_url(item)).query), {"q": [expected]})
        item = {"latitude": BASE.latitude, "longitude": BASE.longitude}
        self.assertEqual(parse_qs(urlsplit(google_search_url(item)).query), {"q": ["-34.6,-58.38"]})
        self.assertEqual(parse_qs(urlsplit(google_maps_url(item)).query)["query"], ["-34.6,-58.38"])

    def test_google_links_encode_accents_enye_spaces_and_query_delimiters(self):
        item = candidate(name="Peña Café & Té", address="Calle Güemes 123, Córdoba #2?x=1+2")
        expected = "Peña Café & Té, Calle Güemes 123, Córdoba #2?x=1+2"
        for builder, key in ((google_maps_url, "query"), (google_search_url, "q")):
            url = builder(item)
            parsed = urlsplit(url)
            self.assertEqual(parse_qs(parsed.query)[key], [expected])
            self.assertEqual(set(parse_qs(parsed.query)), {"api", "query"} if key == "query" else {"q"})
            self.assertEqual(parsed.fragment, "")
            self.assertTrue(url.isascii())
            self.assertNotIn(" ", url)
            self.assertIn("%C3%B1", url)
            self.assertIn("%C3%A9", url)
            self.assertIn("%26", url)

    def test_google_links_can_use_name_and_address_without_coordinates(self):
        item = {"name": "Plaza Juan José Paso", "address": "La Plata, Argentina"}
        expected = "Plaza Juan José Paso, La Plata, Argentina"
        self.assertEqual(parse_qs(urlsplit(google_maps_url(item)).query)["query"], [expected])
        self.assertEqual(parse_qs(urlsplit(google_search_url(item)).query)["q"], [expected])


class OrchestrationTests(SimpleTestCase):
    def setUp(self):
        self.saved = None
        self.active_plan = plan()
        mocks = {
            "plan_service.get_plan": {"side_effect": lambda *a: deepcopy(self.active_plan)},
            "group_service.get_group": {"return_value": {"id": GROUP_ID, "role": "member"}},
            "proposal_service.get_proposal": {"return_value": {
                "tittle": "Merienda", "latitude": BASE.latitude, "longitude": BASE.longitude}},
            "recommendation_store.read": {"side_effect": lambda *a: deepcopy(self.saved)},
            "recommendation_store.claim": {"return_value": TICKET},
            "recommendation_store.finish": {"side_effect": self.finish},
            "GeminiIntentClient": {},
            "GeoapifyClient.search": {"return_value": [candidate(str(i)) for i in range(9)]},
            "GeoapifyClient.geocode": {},
        }
        self.mocks = {}
        for name, kwargs in mocks.items():
            patcher = patch(f"apps.plans.services.plan_recommendation_service.{name}", **kwargs)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.interpret = self.mocks["GeminiIntentClient"].return_value.interpret
        self.interpret.return_value = place_intent_service.validate_intents(
            {"place_intents": ["cafe"]}, model="gemini-3.8-flash")

    def finish(self, plan_value, ticket, **kwargs):
        # Simula el contrato de finish_plan_recommendations: error conserva datos previos.
        if kwargs["error_code"]:
            self.saved = {**(self.saved or row()), "status": "failed",
                          "error_code": kwargs["error_code"]}
        else:
            self.saved = row(status="ready", items=kwargs["items"],
                             criteria=kwargs["criteria"], base_location=kwargs["base_location"])
        return True

    def test_generates_persists_top_five_and_reuses_for_other_members(self):
        first = service.generate_recommendations("jwt-member1", PLAN_ID)
        second = service.generate_recommendations("jwt-member2", PLAN_ID)
        read = service.get_recommendations("jwt-member2", PLAN_ID)
        self.assertEqual(first, second)
        self.assertEqual(first, read)
        self.assertEqual(len(first["items"]), 5)
        self.mocks["GeoapifyClient.search"].assert_called_once()
        self.mocks["GeoapifyClient.geocode"].assert_not_called()
        self.mocks["recommendation_store.finish"].assert_called_once()
        self.interpret.assert_called_once_with(
            self.mocks["proposal_service.get_proposal"].return_value)
        self.assertEqual(self.mocks["proposal_service.get_proposal"].call_args.kwargs,
                         {"plan_id": PLAN_ID, "proposal_id": 47})

    def test_read_without_snapshot_never_generates(self):
        self.assertEqual(service.get_recommendations("jwt", PLAN_ID)["status"], "not_generated")
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.mocks["recommendation_store.claim"].assert_not_called()
        self.interpret.assert_not_called()

    def test_only_confirmed_active_plans(self):
        for changes in ({"selected_proposal_id": None}, {"status": False}, {"confirmed_at": None}):
            self.active_plan = {**plan(), **changes}
            for operation in (service.generate_recommendations, service.get_recommendations):
                self.assertEqual(operation("jwt", PLAN_ID)["status"], "not_confirmed")
        self.mocks["recommendation_store.read"].assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.interpret.assert_not_called()

    def test_outsider_rejected_before_read_or_write(self):
        self.mocks["group_service.get_group"].side_effect = GroupNotFound()
        for operation in (service.generate_recommendations, service.get_recommendations):
            with self.assertRaises(GroupNotFound):
                operation("outsider-jwt", PLAN_ID)
        self.mocks["recommendation_store.read"].assert_not_called()
        self.mocks["recommendation_store.claim"].assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()

    def test_pending_or_failed_snapshots_do_not_retry(self):
        for status in ("pending", "failed"):
            self.saved = row(status=status)
            self.assertEqual(service.generate_recommendations("jwt", PLAN_ID)["status"], status)
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.interpret.assert_not_called()

    def test_claim_lost_to_another_request_does_not_call_provider(self):
        self.mocks["recommendation_store.claim"].return_value = None
        service.generate_recommendations("jwt", PLAN_ID)
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.interpret.assert_not_called()

    def test_provider_failures_persist_codes_without_changing_plan(self):
        for code in ("missing_api_key", "timeout", "http_error", "invalid_response"):
            self.saved = None
            self.mocks["GeoapifyClient.search"].side_effect = RecommendationError(code)
            result = service.generate_recommendations("jwt", PLAN_ID)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_code"], code)
            self.assertEqual(self.active_plan, plan())

    def test_empty_success_is_distinct_and_cached(self):
        self.mocks["GeoapifyClient.search"].return_value = []
        result = service.generate_recommendations("jwt", PLAN_ID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["result_code"], "no_results")
        self.assertIsNone(result["error_code"])
        self.assertEqual(service.generate_recommendations("jwt", PLAN_ID), result)
        self.mocks["GeoapifyClient.search"].assert_called_once()

    def test_fewer_than_five_results_not_filled(self):
        self.mocks["GeoapifyClient.search"].return_value = [candidate()]
        self.assertEqual(len(service.generate_recommendations("jwt", PLAN_ID)["items"]), 1)

    def test_unknown_intent_persisted_without_provider_call(self):
        self.interpret.side_effect = RecommendationError("unsupported_intent")
        result = service.generate_recommendations("jwt", PLAN_ID)
        self.assertEqual(result["error_code"], "unsupported_intent")
        self.mocks["GeoapifyClient.search"].assert_not_called()

    def test_gemini_failures_leave_plan_usable_and_never_call_geoapify(self):
        for code in ("missing_api_key", "timeout", "http_error", "invalid_response", "unsupported_intent"):
            with self.subTest(code=code):
                self.saved = None
                self.interpret.side_effect = RecommendationError(code)
                result = service.generate_recommendations("jwt", PLAN_ID)
                self.assertEqual((result["status"], result["error_code"]), ("failed", code))
                self.assertEqual(self.active_plan, plan())
        self.assertEqual(self.interpret.call_count, 5)
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.mocks["GeoapifyClient.geocode"].assert_not_called()

    def test_explicit_retry_classifies_once_more_without_reconfirming(self):
        self.interpret.side_effect = RecommendationError("timeout")
        service.generate_recommendations("jwt", PLAN_ID)
        service.generate_recommendations("jwt", PLAN_ID)
        self.interpret.assert_called_once()
        self.interpret.side_effect = None
        result = service.generate_recommendations("jwt", PLAN_ID, replace=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.interpret.call_count, 2)
        self.mocks["recommendation_store.claim"].assert_called_with(self.active_plan, replace=True)
        self.mocks["GeoapifyClient.search"].assert_called_once()
        self.assertEqual(self.active_plan, plan())

    def test_gemini_failure_preserves_previous_snapshot_via_existing_finish_contract(self):
        self.saved = row(items=[{**candidate(), "rank": 1}], criteria={"previous": True},
                         base_location={"latitude": BASE.latitude, "longitude": BASE.longitude},
                         generated_at=CONFIRMED.isoformat())
        previous = deepcopy(self.saved)
        self.interpret.side_effect = RecommendationError("timeout")
        result = service.generate_recommendations("jwt", PLAN_ID, replace=True)
        self.assertEqual(result["status"], "failed")
        for field in ("items", "criteria", "base_location", "generated_at"):
            self.assertEqual(self.saved[field], previous[field])
        self.mocks["recommendation_store.finish"].assert_called_once_with(
            self.active_plan, TICKET, items=[], error_code="timeout", base_location=None, criteria=None)

    @override_settings(GEMINI_API_KEY="fake-gemini-test-key")
    def test_real_client_contract_continues_to_one_places_search_and_safe_criteria(self):
        from apps.plans.tests.test_gemini_intent import gemini_response
        self.mocks["proposal_service.get_proposal"].return_value = {
            "tittle": "Tomar algo y después bailar", "description": "Con amigos",
            "created_by": USER_ID, "latitude": BASE.latitude, "longitude": BASE.longitude,
        }
        self.mocks["GeoapifyClient.search"].return_value = [
            candidate("bar", categories=["catering.bar"]),
            candidate("dance", categories=["adult.nightclub"]),
        ]
        with patch.object(service, "GeminiIntentClient", gemini_intent_service.GeminiIntentClient), \
             patch.object(gemini_intent_service.httpx, "post", return_value=gemini_response(
                 {"place_intents": ["bar", "nightclub"]})) as post:
            result = service.generate_recommendations("private-jwt", PLAN_ID)
            service.generate_recommendations("another-member-jwt", PLAN_ID)
            service.get_recommendations("jwt", PLAN_ID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual({i["provider_place_id"] for i in result["items"]}, {"bar", "dance"})
        post.assert_called_once()
        self.mocks["GeoapifyClient.search"].assert_called_once()
        self.assertEqual(self.mocks["GeoapifyClient.search"].call_args.args[0].categories,
                         ("catering.bar", "catering.pub", "adult.nightclub"))
        self.assertEqual(self.saved["criteria"], {
            "interpreter": "gemini", "model": "gemini-3.8-flash", "place_intents": ["bar", "nightclub"],
            "categories": ["catering.bar", "catering.pub", "adult.nightclub"],
            "radius_meters": 5000, "candidate_limit": 20,
        })
        self.assertNotIn("criteria", result)
        self.assertNotIn("private-jwt", str(self.saved))
        self.assertNotIn("Tomar algo", str(self.saved))

    @override_settings(GEMINI_API_KEY="")
    def test_missing_gemini_key_is_controlled_through_real_client(self):
        with patch.object(service, "GeminiIntentClient", gemini_intent_service.GeminiIntentClient), \
             patch.object(gemini_intent_service.httpx, "post") as post:
            result = service.generate_recommendations("jwt", PLAN_ID)
        self.assertEqual(result["error_code"], "missing_api_key")
        post.assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()

    @override_settings(GEMINI_API_KEY="fake-gemini-test-key")
    def test_invalid_real_gemini_output_never_reaches_geoapify(self):
        from apps.plans.tests.test_gemini_intent import gemini_response
        for output, code in (({"place_intents": ["unsupported"]}, "unsupported_intent"),
                             ({"place_intents": ["catering.restaurant"]}, "invalid_response"),
                             ({"place_intents": []}, "invalid_response")):
            with self.subTest(output=output), \
                 patch.object(service, "GeminiIntentClient", gemini_intent_service.GeminiIntentClient), \
                 patch.object(gemini_intent_service.httpx, "post", return_value=gemini_response(output)) as post:
                self.saved = None
                result = service.generate_recommendations("jwt", PLAN_ID)
                self.assertEqual(result["error_code"], code)
                post.assert_called_once()
        self.mocks["GeoapifyClient.search"].assert_not_called()
        self.mocks["GeoapifyClient.geocode"].assert_not_called()

    def test_legacy_ready_snapshot_is_reused_without_gemini_or_rewriting_criteria(self):
        self.saved = row(items=[{**candidate(), "rank": 1}], criteria={"categories": ["catering.cafe"]})
        previous = deepcopy(self.saved)
        result = service.generate_recommendations("jwt", PLAN_ID)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.saved, previous)
        self.interpret.assert_not_called()
        self.mocks["recommendation_store.claim"].assert_not_called()
        self.mocks["recommendation_store.finish"].assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()

    def test_reopened_during_search_does_not_return_stale_snapshot(self):
        def reopened(criteria):
            self.active_plan["selected_proposal_id"] = None
            return [candidate()]
        self.mocks["GeoapifyClient.search"].side_effect = reopened
        self.assertEqual(service.generate_recommendations("jwt", PLAN_ID)["status"], "not_confirmed")

    def test_public_snapshot_omits_internal_and_unknown_fields(self):
        self.saved = row(items=[{**candidate(), "rank": 1, "raw": "hidden"}], generation_id=TICKET)
        result = service.get_recommendations("jwt", PLAN_ID)
        self.assertNotIn(TICKET, str(result))
        self.assertNotIn("hidden", str(result))

    def test_old_snapshot_gets_both_links_without_rewriting_or_provider_calls(self):
        self.saved = row(items=[{**candidate(name="El Porteño", address="City Bell"), "rank": 1}])
        original = deepcopy(self.saved)
        result = service.get_recommendations("jwt", PLAN_ID)
        item = result["items"][0]
        self.assertEqual(parse_qs(urlsplit(item["google_maps_url"]).query)["query"], ["El Porteño, City Bell"])
        self.assertEqual(parse_qs(urlsplit(item["google_search_url"]).query)["q"], ["El Porteño, City Bell"])
        self.assertEqual(self.saved, original)
        self.mocks["recommendation_store.finish"].assert_not_called()
        self.mocks["recommendation_store.claim"].assert_not_called()
        self.mocks["GeoapifyClient.search"].assert_not_called()


class RecommendationSDKTests(SimpleTestCase):
    def setUp(self):
        self.requests, self.responses, self.transports = [], [], []

        def handle(request):
            self.requests.append(request)
            status, payload = self.responses.pop(0)
            return httpx.Response(status, json=payload)

        def factory():
            transport = httpx.Client(transport=httpx.MockTransport(handle))
            self.transports.append(transport)
            return transport

        patcher = patch.object(clients, "http_client", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertFalse(self.responses)
        self.assertTrue(all(t.is_closed for t in self.transports))

    def test_read_uses_user_jwt_and_confirmation_filter(self):
        self.responses = [(200, [row()])]
        self.assertEqual(store.read("private-jwt", plan())["status"], "ready")
        request = self.requests[0]
        self.assertEqual(request.headers["Authorization"], "Bearer private-jwt")
        self.assertEqual(request.url.params["plan_id"], "eq." + PLAN_ID)
        self.assertIn("confirmed_at", request.url.params)
        self.assertNotIn("generation_id", request.url.params["select"])

    def test_claim_and_finish_use_backend_rpc_without_user_jwt(self):
        self.responses = [(200, TICKET), (200, True)]
        self.assertEqual(store.claim(plan()), TICKET)
        self.assertTrue(store.finish(plan(), TICKET, items=[]))
        for request in self.requests:
            self.assertEqual(request.headers["apikey"], "sb_secret_test_placeholder")
            self.assertNotIn("Authorization", request.headers)
        self.assertEqual(self.requests[0].url.path, "/rest/v1/rpc/claim_plan_recommendations")
        self.assertEqual(self.requests[1].url.path, "/rest/v1/rpc/finish_plan_recommendations")

    def test_missing_migration_or_storage_http_error_is_controlled(self):
        self.responses = [(404, {"code": "42P01", "message": "missing table"})]
        with self.assertRaises(RecommendationError) as caught:
            store.read("jwt", plan())
        self.assertEqual(caught.exception.code, "storage_unavailable")

    def test_expired_read_session_remains_session_expired(self):
        self.responses = [(401, {"code": "PGRST301", "message": "expired", "details": None, "hint": None})]
        with self.assertRaises(SessionExpired):
            store.read("expired", plan())

    def test_malformed_stored_snapshot_is_controlled(self):
        for malformed in (row(items=[{}]), row(items=[candidate()] * 6), row(status="invented")):
            self.responses = [(200, [malformed])]
            with self.assertRaises(RecommendationError) as caught:
                store.read("jwt", plan())
            self.assertEqual(caught.exception.code, "storage_unavailable")

    def test_invalid_backend_ticket_is_controlled(self):
        self.responses = [(200, "invalid-ticket")]
        with self.assertRaises(RecommendationError):
            store.claim(plan())


@override_settings(GEOAPIFY_API_KEY="fake-geoapify-test-key", GEMINI_API_KEY="fake-gemini-test-key")
class RecommendationFlowTests(SimpleTestCase):
    """Flujo completo de servicios y SDK; sólo la red remota se simula."""
    def test_member_generation_then_other_member_read_reuses_snapshot(self):
        saved, calls, requests = {}, [], []

        def remote(request):
            requests.append(request)
            path = request.url.path
            if path == "/rest/v1/plans":
                payload = [plan(confirmed_at=CONFIRMED.isoformat())]
            elif path == "/rest/v1/groups":
                payload = [{"id": GROUP_ID, "name": "Grupo", "description": None,
                            "created_by": USER_ID, "created_at": CONFIRMED.isoformat(),
                            "updated_at": CONFIRMED.isoformat()}]
            elif path == "/rest/v1/group_members":
                payload = [{"role": "member"}]
            elif path == "/rest/v1/proposals":
                from apps.plans.tests.test_services import proposal_row
                payload = [proposal_row()]
            elif path == "/rest/v1/plan_recommendations":
                payload = list(saved.values())
            elif path == "/rest/v1/rpc/claim_plan_recommendations":
                saved[PLAN_ID] = row(status="pending")
                payload = TICKET
            elif path == "/rest/v1/rpc/finish_plan_recommendations":
                body = json.loads(request.content)
                self.assertIsNone(body["p_error_code"])
                saved[PLAN_ID] = row(items=body["p_items"], criteria=body["p_criteria"])
                payload = True
            else:
                raise AssertionError(f"Solicitud inesperada: {path}")
            return httpx.Response(200, json=payload)

        def factory():
            return httpx.Client(transport=httpx.MockTransport(remote))

        def places(url, **kwargs):
            calls.append(url)
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [feature()]},
                                  request=httpx.Request("GET", url))

        from apps.plans.tests.test_gemini_intent import gemini_response
        with patch.object(clients, "http_client", side_effect=factory), \
                patch.object(geoapify_service.httpx, "get", side_effect=places), \
                patch.object(gemini_intent_service.httpx, "post", return_value=gemini_response(
                    {"place_intents": ["cafe"]})) as post:
            first = service.generate_recommendations("member1-jwt", PLAN_ID)
            second = service.generate_recommendations("member2-jwt", PLAN_ID)
            third = service.get_recommendations("member2-jwt", PLAN_ID)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(calls, [geoapify_service.PLACES_URL])
        post.assert_called_once()
        self.assertEqual(saved[PLAN_ID]["criteria"]["interpreter"], "gemini")
        self.assertEqual(saved[PLAN_ID]["criteria"]["place_intents"], ["cafe"])
        self.assertEqual(sum(r.url.path.endswith("claim_plan_recommendations") for r in requests), 1)
        self.assertNotIn("fake-geoapify-test-key", str(saved))
        self.assertNotIn("fake-geoapify-test-key", str(first))
        self.assertNotIn("fake-gemini-test-key", str(saved))
        self.assertNotIn("fake-gemini-test-key", str(first))
        self.assertTrue(all("/rpc/" in r.url.path or r.headers["apikey"] == "sb_publishable_test_placeholder"
                            for r in requests))


class RecommendationViewsTests(TestCase):
    def setUp(self):
        self.url = f"/planes/{PLAN_ID}/recomendaciones/"
        for name, value in (("current_user", {"id": USER_ID, "username": "Ana"}),
                            ("get_access_token", "private-jwt")):
            patcher = patch(f"apps.users.services.session_service.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch.object(service, "get_recommendations", return_value={"status": "not_generated", "items": []})
    @patch.object(service, "generate_recommendations")
    def test_get_reads_without_generation_and_is_private(self, generate, read):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        read.assert_called_once_with("private-jwt", PLAN_ID)
        generate.assert_not_called()
        self.assertIn("no-store", response.headers["Cache-Control"])

    @patch.object(service, "generate_recommendations", return_value={"status": "ready", "items": []})
    def test_post_ignores_frontend_plan_location_and_replace(self, generate):
        response = self.client.post(self.url, {"plan_id": "other", "latitude": 90, "replace": True})
        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with("private-jwt", PLAN_ID)

    @patch.object(service, "get_recommendations", side_effect=GroupNotFound())
    def test_outsider_get_is_404(self, read):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    @patch.object(service, "generate_recommendations", side_effect=PlanNotFound())
    def test_outsider_post_is_404(self, generate):
        self.assertEqual(self.client.post(self.url).status_code, 404)

    @patch.object(service, "generate_recommendations")
    def test_post_requires_csrf(self, generate):
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url).status_code, 403)
        generate.assert_not_called()

    @patch.object(service, "get_recommendations", side_effect=RecommendationError("storage_unavailable"))
    def test_storage_failure_has_safe_503(self, read):
        self.assertEqual(self.client.get(self.url).status_code, 503)

    @patch.object(service, "generate_recommendations", return_value={
        "status": "failed", "error_code": "missing_api_key", "items": [],
    })
    def test_provider_failure_is_consultable_without_500(self, generate):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error_code"], "missing_api_key")

    @patch("apps.users.services.session_service.current_user", return_value=None)
    def test_anonymous_is_redirected_to_login(self, current):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_unsupported_method_rejected(self):
        self.assertEqual(self.client.put(self.url).status_code, 405)
