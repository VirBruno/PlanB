"""Ranking objetivo: compatibilidad, distancia geodésica, completitud y ID."""
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode


def distance_meters(base, latitude, longitude):
    lat1, lat2 = radians(base.latitude), radians(latitude)
    dlat, dlon = lat2 - lat1, radians(longitude - base.longitude)
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    return 6371008.8 * 2 * asin(sqrt(min(1.0, max(0.0, a))))


def rank_candidates(candidates, criteria):
    ranked = []
    for item in candidates:
        exact = any(c in item["categories"] for c in criteria.categories)
        compatible = exact or any(
            category.startswith(requested + ".")
            for category in item["categories"] for requested in criteria.categories)
        if not compatible:
            continue
        distance = distance_meters(criteria.location, item["latitude"], item["longitude"])
        if distance > criteria.radius_meters:
            continue
        completeness = sum(bool(item.get(field)) for field in ("address", "website", "opening_hours"))
        ranked.append(((0 if exact else 1, distance, -completeness, item["provider_place_id"]),
                       {**item, "distance_meters": round(distance, 2)}))
    seen, result = set(), []
    for _, item in sorted(ranked, key=lambda entry: entry[0]):
        identifier = item["provider_place_id"]
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append({**item, "rank": len(result) + 1})
        if len(result) == 5:
            break
    return result


def _google_place_query(item):
    name = item.get("name")
    address = item.get("address")
    name = name.strip() if isinstance(name, str) else ""
    address = address.strip() if isinstance(address, str) else ""
    if name and address:
        return f"{name}, {address}"
    coordinates = f"{item['latitude']},{item['longitude']}"
    return f"{name}, {coordinates}" if name else coordinates


def google_maps_url(item):
    # Búsqueda externa: sin Google place_id no se garantiza una ficha específica.
    return "https://www.google.com/maps/search/?" + urlencode({
        "api": 1, "query": _google_place_query(item),
    })


def google_search_url(item):
    return "https://www.google.com/search?" + urlencode({"q": _google_place_query(item)})
