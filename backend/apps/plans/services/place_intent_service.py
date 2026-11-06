"""Catálogo semántico de Plan B, validación y mapping controlado a Places."""
from dataclasses import dataclass

from .place_types import Location, RecommendationError


INTENT_CATEGORIES = {
    "restaurant": ("catering.restaurant",),
    "cafe": ("catering.cafe",),
    "bar": ("catering.bar", "catering.pub"),
    "nightclub": ("adult.nightclub",),
    "park": ("leisure.park", "leisure.picnic"),
    "supermarket": ("commercial.supermarket",),
    "cinema": ("entertainment.cinema",),
    "bakery": ("commercial.food_and_drink.bakery",),
    "fast_food": ("catering.fast_food",),
    "shopping": ("commercial.shopping_mall",),
}
MAX_INTENTS = 3
INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "place_intents": {
            "type": "array", "minItems": 1, "maxItems": MAX_INTENTS,
            "items": {"type": "string", "enum": [*INTENT_CATEGORIES, "unsupported"]},
        },
    },
    "required": ["place_intents"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class IntentClassification:
    place_intents: tuple[str, ...]
    model: str

    @property
    def categories(self):
        return tuple(dict.fromkeys(
            category for intent in self.place_intents for category in INTENT_CATEGORIES[intent]))

    def criteria(self):
        return {"interpreter": "gemini", "model": self.model,
                "place_intents": list(self.place_intents)}


def validate_intents(payload, *, model):
    # Revalidar todo el contrato aunque Gemini aplique JSON Schema.
    if not isinstance(payload, dict) or set(payload) != {"place_intents"}:
        raise RecommendationError("invalid_response")
    intents = payload["place_intents"]
    if (not isinstance(intents, list) or not 1 <= len(intents) <= MAX_INTENTS
            or any(not isinstance(i, str) or i not in (*INTENT_CATEGORIES, "unsupported")
                   for i in intents)
            or len(set(intents)) != len(intents)
            or "unsupported" in intents and len(intents) != 1):
        raise RecommendationError("invalid_response")
    if intents == ["unsupported"]:
        raise RecommendationError("unsupported_intent")
    return IntentClassification(tuple(intents), model)


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
