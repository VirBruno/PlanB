"""Zona urbana adaptable; compatibilidad, datos presentes y distancia/diversidad."""
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode

from .place_types import Location

MIN_SEPARATION_METERS = 150


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
        # Cantidad de datos objetivos presentes, no una valoración del comercio.
        fields_present = sum(bool(item.get(field)) for field in ("address", "website", "opening_hours"))
        specific_category = any(
            category.startswith(requested + ".")
            for category in item["categories"] for requested in criteria.categories)
        fields_present += int(specific_category)
        ranked.append((0 if exact else 1, -fields_present, distance, item["provider_place_id"], item))

    # Deduplicar antes de decidir si la zona tiene cinco alternativas disponibles.
    # Los textos sólo desempatan duplicados del MISMO ID con igual completitud.
    unique = {}
    for entry in sorted(ranked, key=lambda e: e[:4] + tuple(
            str(e[4].get(field) or "") for field in ("name", "address", "website", "opening_hours"))):
        unique.setdefault(entry[3], entry)
    available = list(unique.values())
    if not available:
        return []
    distances = sorted(entry[2] for entry in available)
    # No exigir 300 m de distancia mínima ni descartar los pocos lugares próximos.
    # Si no alcanzan cinco dentro de 600 m, ampliar sólo hasta el quinto disponible
    # (o el último si hay menos), siempre dentro del límite de la búsqueda original.
    area_radius = max(criteria.preferred_radius_meters, distances[min(4, len(distances) - 1)])
    pool = [entry for entry in available if entry[2] <= area_radius]
    selected, result = [], []
    while pool and len(result) < 5:
        def preference(entry):
            item = entry[4]
            clustered = any(distance_meters(
                Location(chosen["latitude"], chosen["longitude"]), item["latitude"], item["longitude"],
            ) < MIN_SEPARATION_METERS for chosen in selected)
            # La separación sólo desempata categoría y datos equivalentes.
            return entry[:2] + (clustered,) + entry[2:4]

        entry = min(pool, key=preference)
        pool.remove(entry)
        item = entry[4]
        selected.append(item)
        result.append({**item, "distance_meters": round(entry[2], 2), "rank": len(result) + 1})
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
