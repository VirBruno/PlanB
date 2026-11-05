"""Cliente backend sin conocimiento de planes, usuarios, IA o persistencia."""
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .place_types import Location, RecommendationError

PLACES_URL = "https://api.geoapify.com/v2/places"
GEOCODING_URL = "https://api.geoapify.com/v1/geocode/search"


def _text(value, limit=2000):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _website(value):
    value = _text(value)
    if value:
        try:
            parsed = urlsplit(value)
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username:
                return value
        except ValueError:
            pass
    return None


class GeoapifyClient:
    @sensitive_variables()
    def _features(self, url, params):
        key = getattr(settings, "GEOAPIFY_API_KEY", "")
        if not key:
            raise RecommendationError("missing_api_key")
        try:
            response = httpx.get(
                url, params={**params, "apiKey": key},
                timeout=httpx.Timeout(6.0, connect=2.0), follow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if (not isinstance(payload, dict) or payload.get("type") != "FeatureCollection"
                    or not isinstance(payload.get("features"), list)):
                raise ValueError
            return payload["features"]
        except httpx.TimeoutException:
            raise RecommendationError("timeout") from None
        except httpx.HTTPError:
            raise RecommendationError("http_error") from None
        except (ValueError, TypeError):
            raise RecommendationError("invalid_response") from None

    def geocode(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            raise RecommendationError("missing_location")
        features = self._features(GEOCODING_URL, {"text": text, "limit": 1, "lang": "es"})
        if not features:
            raise RecommendationError("location_not_found")
        try:
            properties = features[0]["properties"]
            return Location(properties["lat"], properties["lon"])
        except (KeyError, TypeError, ValueError):
            raise RecommendationError("invalid_response") from None

    def search(self, criteria):
        loc = criteria.location
        features = self._features(PLACES_URL, {
            "categories": ",".join(criteria.categories),
            "filter": f"circle:{loc.longitude},{loc.latitude},{criteria.radius_meters}",
            "bias": f"proximity:{loc.longitude},{loc.latitude}",
            "limit": criteria.candidate_limit, "lang": "es",
        })
        candidates = []
        for feature in features[:criteria.candidate_limit]:
            candidate = self._candidate(feature)
            if candidate:
                candidates.append(candidate)
        if features and not candidates:
            raise RecommendationError("invalid_response")
        return candidates

    @staticmethod
    def _candidate(feature):
        try:
            p = feature["properties"]
            if not isinstance(p, dict):
                return None
            location = Location(p["lat"], p["lon"])
            identifier, name = _text(p.get("place_id"), 512), _text(p.get("name"), 300)
            categories = p.get("categories")
            if (not identifier or not name or not isinstance(categories, list)
                    or not categories or any(not isinstance(c, str) for c in categories)):
                return None
            # Datos opcionales sólo cuando realmente vinieron en Places. No Details.
            datasource = p.get("datasource")
            raw = datasource.get("raw") if isinstance(datasource, dict) else None
            raw = raw if isinstance(raw, dict) else {}
            return {
                "provider": "geoapify", "provider_place_id": identifier,
                "name": name, "categories": categories[:30], "address": _text(p.get("formatted")),
                "latitude": location.latitude, "longitude": location.longitude,
                "website": _website(p.get("website")) or _website(raw.get("website")),
                "opening_hours": _text(p.get("opening_hours")) or _text(raw.get("opening_hours")),
                "media": None,
            }
        except (KeyError, TypeError, ValueError):
            return None
