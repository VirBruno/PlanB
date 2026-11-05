"""Resolver temporal determinístico; reemplazable por un productor de categorías."""
import re
import unicodedata

from .place_types import Location, RecommendationError


def resolve_categories(proposal):
    # Sólo contenido de la propuesta elegida, nunca todas las alternativas.
    text = f"{proposal.get('tittle') or ''} {proposal.get('description') or ''}"
    words = set(re.findall(r"[a-z]+", "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c))))
    rules = (
        ({"cena", "cenar", "comer", "restaurante", "restaurantes"}, ("catering.restaurant",)),
        ({"cafe", "merienda", "merendar"}, ("catering.cafe",)),
        ({"picnic", "parque", "plaza"}, ("leisure.park", "leisure.picnic")),
        ({"supermercado", "comprar", "compras"}, ("commercial.supermarket",)),
    )
    matches = [categories for keywords, categories in rules if words & keywords]
    if len(matches) != 1:
        raise RecommendationError("unsupported_intent")
    return matches[0]


def resolve_location(proposal, geocoder):
    try:
        return Location(proposal.get("latitude"), proposal.get("longitude"))
    except ValueError:
        pass
    # En el esquema actual posicion es PostGIS obligatorio y get_proposal ya lo
    # normaliza. No inferir una dirección desde el título ni la localidad tentativa.
    # Este contrato admite una ubicación textual verificada por un futuro adaptador.
    address = proposal.get("location_text")
    if not isinstance(address, str) or not address.strip():
        raise RecommendationError("missing_location")
    return geocoder.geocode(address.strip())
