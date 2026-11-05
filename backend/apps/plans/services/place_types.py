"""Contrato estructurado compartido por resolver, proveedor y ranking."""
from dataclasses import dataclass
from math import isfinite
import re


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float

    def __post_init__(self):
        for value, bound in ((self.latitude, 90), (self.longitude, 180)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not isfinite(value) or not -bound <= value <= bound):
                raise ValueError("Coordenadas inválidas")


@dataclass(frozen=True)
class PlaceSearch:
    categories: tuple[str, ...]
    location: Location
    radius_meters: int = 5000
    candidate_limit: int = 20

    def __post_init__(self):
        if (not isinstance(self.location, Location)
                or not isinstance(self.categories, tuple) or not 1 <= len(self.categories) <= 5
                or any(not isinstance(c, str) or not re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)*", c)
                       for c in self.categories)
                or type(self.radius_meters) is not int or not 100 <= self.radius_meters <= 20000
                or type(self.candidate_limit) is not int or not 1 <= self.candidate_limit <= 20):
            raise ValueError("Criterio de búsqueda inválido")


class RecommendationError(Exception):
    """Sólo códigos internos estables, nunca URLs o mensajes del proveedor."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)
