"""Pronóstico para propuestas: Open-Meteo, sin guardar datos en Supabase.

Se consulta una fecha concreta dentro de los próximos 16 días; nunca se
presentan datos históricos o estacionales como si fueran un pronóstico.
"""
from datetime import timedelta
from math import isfinite
from zoneinfo import ZoneInfo

import httpx
from django.core.cache import cache
from django.utils import timezone

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
CACHE_TTL_SECONDS = 30 * 60

CONDITIONS = {
    0: ("Despejado", "sun"),
    1: ("Mayormente despejado", "sun-cloud"),
    2: ("Parcialmente nublado", "sun-cloud"),
    3: ("Nublado", "cloud"),
    45: ("Niebla", "cloud"), 48: ("Niebla", "cloud"),
    51: ("Llovizna", "rain"), 53: ("Llovizna", "rain"), 55: ("Llovizna", "rain"),
    56: ("Llovizna helada", "rain"), 57: ("Llovizna helada", "rain"),
    61: ("Lluvia", "rain"), 63: ("Lluvia", "rain"), 65: ("Lluvia intensa", "rain"),
    66: ("Lluvia helada", "rain"), 67: ("Lluvia helada", "rain"),
    71: ("Nieve", "snow"), 73: ("Nieve", "snow"), 75: ("Nieve intensa", "snow"),
    77: ("Granizo fino", "snow"),
    80: ("Chaparrones", "rain"), 81: ("Chaparrones", "rain"), 82: ("Chaparrones intensos", "rain"),
    85: ("Nieve", "snow"), 86: ("Nieve intensa", "snow"),
    95: ("Tormentas", "storm"), 96: ("Tormentas con granizo", "storm"),
    99: ("Tormentas con granizo", "storm"),
}


class WeatherUnavailable(Exception):
    """Error de red o respuesta incompleta del proveedor meteorológico."""


def _finite_number(value, *, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise WeatherUnavailable()
    if lower is not None and value < lower or upper is not None and value > upper:
        raise WeatherUnavailable()
    return value


def forecast_for_proposal(proposal):
    """Devuelve un estado consultable o el pronóstico diario de la propuesta.

    La fecha del plan y la zona horaria corresponden a Argentina (fase 1).
    Ningún parámetro externo decide el host remoto (evita SSRF).
    """
    meeting = proposal.get("date_pick")
    if not meeting:
        return {"status": "missing_date", "message": "Agregá una fecha para consultar el clima."}

    if timezone.is_aware(meeting):
        meeting = timezone.localtime(meeting, ARGENTINA_TZ)
    target = meeting.date()
    today = timezone.localdate(timezone=ARGENTINA_TZ)
    if target < today:
        return {"status": "past", "message": "La fecha ya pasó; no se muestra un pronóstico."}
    if target >= today + timedelta(days=16):
        return {"status": "too_early", "message": "El pronóstico estará disponible cuando falten 16 días o menos."}

    lat = _finite_number(proposal.get("latitude"), lower=-90, upper=90)
    lon = _finite_number(proposal.get("longitude"), lower=-180, upper=180)
    # Coordenadas redondeadas para compartir caché entre ubicaciones cercanas.
    key = f"planb:weather:v1:{lat:.3f}:{lon:.3f}:{target.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        response = httpx.get(
            FORECAST_URL,
            params={
                "latitude": lat, "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                "timezone": "America/Argentina/Buenos_Aires",
                "start_date": target.isoformat(), "end_date": target.isoformat(),
            },
            timeout=httpx.Timeout(5.0, connect=2.0),
            follow_redirects=False,
        )
        response.raise_for_status()
        daily = response.json()["daily"]
        if daily["time"] != [target.isoformat()]:
            raise WeatherUnavailable()
        code = int(_finite_number(daily["weather_code"][0], lower=0, upper=99))
        high = _finite_number(daily["temperature_2m_max"][0], lower=-100, upper=70)
        low = _finite_number(daily["temperature_2m_min"][0], lower=-100, upper=70)
        rain_raw = daily["precipitation_probability_max"][0]
        wind_raw = daily["wind_speed_10m_max"][0]
        rain = None if rain_raw is None else round(_finite_number(rain_raw, lower=0, upper=100))
        wind = None if wind_raw is None else round(_finite_number(wind_raw, lower=0, upper=400))
        if low > high:
            raise WeatherUnavailable()
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError, WeatherUnavailable) as exc:
        raise WeatherUnavailable() from exc

    label, icon = CONDITIONS.get(code, ("Condiciones variables", "cloud"))
    result = {
        "status": "available", "date": target.isoformat(), "condition": label,
        "icon": icon, "temperature_max": round(high), "temperature_min": round(low),
        "rain_probability": rain, "wind_max": wind,
    }
    cache.set(key, result, CACHE_TTL_SECONDS)
    return result
