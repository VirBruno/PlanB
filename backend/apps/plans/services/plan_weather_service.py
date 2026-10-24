"""Buscador meteorológico por provincia y localidad para planes (sin persistencia)."""
import hashlib
import re
import unicodedata
from datetime import timedelta, date, datetime, time

import httpx
from django.core.cache import cache
from django.utils import timezone

from .weather_service import (
    ARGENTINA_TZ, CONDITIONS, FORECAST_URL, WeatherUnavailable, _finite_number,
)

GEOCODING_SEARCH_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODING_GET_URL = "https://geocoding-api.open-meteo.com/v1/get"
FORECAST_CACHE_TTL = 30 * 60
GEOCODING_CACHE_TTL = 60 * 60

# Los nombres corresponden a las 23 provincias y la Ciudad Autónoma de Buenos Aires.
PROVINCES = {
    "buenos-aires": "Buenos Aires", "caba": "Ciudad Autónoma de Buenos Aires",
    "catamarca": "Catamarca", "chaco": "Chaco", "chubut": "Chubut",
    "cordoba": "Córdoba", "corrientes": "Corrientes", "entre-rios": "Entre Ríos",
    "formosa": "Formosa", "jujuy": "Jujuy", "la-pampa": "La Pampa",
    "la-rioja": "La Rioja", "mendoza": "Mendoza", "misiones": "Misiones",
    "neuquen": "Neuquén", "rio-negro": "Río Negro", "salta": "Salta",
    "san-juan": "San Juan", "san-luis": "San Luis", "santa-cruz": "Santa Cruz",
    "santa-fe": "Santa Fe", "santiago-del-estero": "Santiago del Estero",
    "tierra-del-fuego": "Tierra del Fuego", "tucuman": "Tucumán",
}


def _norm(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                      if not unicodedata.combining(c)).replace(".", " ").split())


def _matches_province(province_code, admin1):
    if not isinstance(admin1, str):
        return False
    admin = _norm(admin1)
    if province_code == "caba":
        return admin in {
            "buenos aires fd", "buenos aires f d", "capital federal",
            "ciudad autonoma de buenos aires", "ciudad de buenos aires",
            "buenos aires city", "federal district", "distrito federal",
        }
    if province_code == "buenos-aires":
        return admin in {"buenos aires", "provincia de buenos aires", "buenos aires province"}
    return admin == _norm(PROVINCES[province_code])


def _get_json(url, params):
    try:
        response = httpx.get(url, params=params, timeout=httpx.Timeout(6.0, connect=2.0), follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            raise WeatherUnavailable()
        return payload
    except (httpx.HTTPError, ValueError, TypeError, WeatherUnavailable) as exc:
        raise WeatherUnavailable() from exc


def _city_from_record(city, code):
    if not isinstance(city, dict) or city.get("country_code") != "AR" or not _matches_province(code, city.get("admin1")):
        return None
    try:
        identifier = city["id"]
        if isinstance(identifier, bool) or not isinstance(identifier, int) or not 0 < identifier < 2**31:
            return None
        lat = _finite_number(city["latitude"], lower=-56, upper=-21)
        lon = _finite_number(city["longitude"], lower=-74, upper=-53)
        name = city["name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            return None
        return {"id": identifier, "name": name, "province": PROVINCES[code], "latitude": lat, "longitude": lon}
    except (KeyError, TypeError, WeatherUnavailable):
        return None


def search_cities(province_code, query):
    if province_code not in PROVINCES or not isinstance(query, str):
        raise ValueError("Provincia o búsqueda inválida")
    # Rechazar primero saltos de línea y controles: strip() los borraría en los bordes
    # y \s en la expresión regular anterior admitía tabuladores/saltos internos.
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in query):
        raise ValueError("La búsqueda contiene caracteres inválidos.")
    query = query.strip()
    if not 3 <= len(query) <= 70 or not re.fullmatch(r"[\w .'áéíóúÁÉÍÓÚüÜñÑ-]+", query, re.UNICODE):
        raise ValueError("Escribí al menos 3 letras del nombre de una localidad.")
    # Las claves de Django también deben servir para Memcached: nada de espacios,
    # incluso si la búsqueda tiene varias palabras (p.ej. 'Mar del Plata').
    query_digest = hashlib.sha256(_norm(query).encode("utf-8")).hexdigest()
    key = f"planb:city-search:v3:{province_code}:{query_digest}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    qualifier = "Buenos Aires F.D." if province_code == "caba" else PROVINCES[province_code]
    # Se solicita una provincia como contexto y se valida el admin1 devuelto.
    payload = _get_json(GEOCODING_SEARCH_URL, {"name": f"{query}, {qualifier}", "count": 30, "countryCode": "AR", "language": "es"})
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise WeatherUnavailable()
    # Algunas provincias se registran con alias en GeoNames. Si el calificador
    # no produce coincidencias, buscamos sin él y filtramos estrictamente admin1.
    if not any(_city_from_record(record, province_code) for record in results):
        fallback = _get_json(GEOCODING_SEARCH_URL, {"name": query, "count": 50, "countryCode": "AR", "language": "es"})
        results = fallback.get("results", [])
        if not isinstance(results, list):
            raise WeatherUnavailable()
    cities = []
    seen = set()
    for record in results:
        city = _city_from_record(record, province_code)
        if city is not None and city["id"] not in seen:
            cities.append({"id": city["id"], "name": city["name"], "province": city["province"]})
            seen.add(city["id"])
        if len(cities) == 12:
            break
    cache.set(key, cities, GEOCODING_CACHE_TTL)
    return cities


def resolve_city(province_code, city_id):
    """Verifica en Open-Meteo que el ID pertenece a Argentina y a la provincia."""
    if province_code not in PROVINCES or isinstance(city_id, bool) or not isinstance(city_id, int) or not 0 < city_id < 2**31:
        raise ValueError("Localidad o provincia inválida")
    city_key = f"planb:city-id:v2:{province_code}:{city_id}"
    city = cache.get(city_key)
    if city is None:
        record = _get_json(GEOCODING_GET_URL, {"id": city_id, "language": "es"})
        city = _city_from_record(record, province_code)
        if city is None:
            raise ValueError("La localidad no corresponde a la provincia elegida.")
        cache.set(city_key, city, GEOCODING_CACHE_TTL)
    return city


def forecast_for_datetime(province_code, city_id, plan_date, plan_time):
    """Pronóstico horario para la fecha/hora local de Argentina elegidas.

    Para días anteriores o después del día 15 nunca se retorna clima actual
    como si fuera un pronóstico futuro. La agenda se puede guardar igual.
    """
    if not isinstance(plan_date, date) or isinstance(plan_date, datetime) or not isinstance(plan_time, time):
        raise ValueError("Fecha u horario inválidos")
    today = timezone.localdate(timezone=ARGENTINA_TZ)
    if plan_date < today:
        return {"status": "past", "message": "La fecha ya pasó; no hay pronóstico futuro."}
    if plan_date >= today + timedelta(days=16):
        return {"status": "too_early", "message": "El pronóstico aparecerá cuando falten 16 días o menos."}
    city = resolve_city(province_code, city_id)
    target_hour = plan_time.hour
    key = f"planb:city-hourly:v1:{city_id}:{plan_date.isoformat()}:{target_hour}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = _get_json(FORECAST_URL, {
        "latitude": city["latitude"], "longitude": city["longitude"],
        "timezone": "America/Argentina/Buenos_Aires",
        "start_date": plan_date.isoformat(), "end_date": plan_date.isoformat(),
        "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
    })
    try:
        hourly = payload["hourly"]
        times = hourly["time"]
        stamp = f"{plan_date.isoformat()}T{target_hour:02d}:00"
        index = times.index(stamp)
        for name in ("temperature_2m", "precipitation_probability", "weather_code", "wind_speed_10m"):
            if len(hourly[name]) != len(times):
                raise WeatherUnavailable()
        degree = _finite_number(hourly["temperature_2m"][index], lower=-100, upper=70)
        code = int(_finite_number(hourly["weather_code"][index], lower=0, upper=99))
        raw_rain = hourly["precipitation_probability"][index]
        raw_wind = hourly["wind_speed_10m"][index]
        rain = None if raw_rain is None else round(_finite_number(raw_rain, lower=0, upper=100))
        wind = None if raw_wind is None else round(_finite_number(raw_wind, lower=0, upper=400))
    except (KeyError, ValueError, TypeError, IndexError, WeatherUnavailable, AttributeError) as exc:
        raise WeatherUnavailable() from exc
    condition, icon = CONDITIONS.get(code, ("Condiciones variables", "cloud"))
    result = {"status": "available", "city": city["name"], "province": city["province"],
              "date": plan_date.isoformat(), "time": f"{target_hour:02d}:00",
              "condition": condition, "icon": icon, "temperature": round(degree),
              "rain_probability": rain, "wind": wind}
    cache.set(key, result, FORECAST_CACHE_TTL)
    return result


def forecast_for_city(province_code, city_id):
    city = resolve_city(province_code, city_id)
    today = timezone.localdate(timezone=ARGENTINA_TZ)
    key = f"planb:city-forecast:v2:{city_id}:{today.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    payload = _get_json(FORECAST_URL, {
        "latitude": city["latitude"], "longitude": city["longitude"],
        "timezone": "America/Argentina/Buenos_Aires", "forecast_days": 16,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
    })
    try:
        daily = payload["daily"]
        fields = ("time", "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max", "wind_speed_10m_max")
        if any(not isinstance(daily[field], list) or len(daily[field]) != 16 for field in fields):
            raise WeatherUnavailable()
        days = []
        for i in range(16):
            expected = (today + timedelta(days=i)).isoformat()
            if daily["time"][i] != expected:
                raise WeatherUnavailable()
            code = int(_finite_number(daily["weather_code"][i], lower=0, upper=99))
            high = _finite_number(daily["temperature_2m_max"][i], lower=-100, upper=70)
            low = _finite_number(daily["temperature_2m_min"][i], lower=-100, upper=70)
            if low > high:
                raise WeatherUnavailable()
            rain_raw = daily["precipitation_probability_max"][i]
            wind_raw = daily["wind_speed_10m_max"][i]
            rain = None if rain_raw is None else round(_finite_number(rain_raw, lower=0, upper=100))
            wind = None if wind_raw is None else round(_finite_number(wind_raw, lower=0, upper=400))
            condition, icon = CONDITIONS.get(code, ("Condiciones variables", "cloud"))
            days.append({"date": expected, "condition": condition, "icon": icon,
                         "temperature_min": round(low), "temperature_max": round(high),
                         "rain_probability": rain, "wind_max": wind})
    except (KeyError, TypeError, IndexError, ValueError, WeatherUnavailable) as exc:
        raise WeatherUnavailable() from exc
    result = {"status": "available", "city": city["name"], "province": city["province"], "days": days}
    cache.set(key, result, FORECAST_CACHE_TTL)
    return result
