"""Feriados argentinos generales, cargados por año; nunca consulta la red.

Fuentes oficiales verificadas el 26/10/2026:
https://www.argentina.gob.ar/feriados
https://ejapo.cancilleria.gob.ar/es/content/feriados-y-diferencia-horaria
https://www.argentina.gob.ar/normativa/nacional/norma-421799/texto
https://www.argentina.gob.ar/normativa/nacional/norma-430580/texto (art. 2)

Las fechas son las observadas, incluidos traslados. No se extrapolan otros años.
No incluye celebraciones limitadas a una religión ni excepciones territoriales.
Para agregar un año, incorporar su tabla verificada a HOLIDAYS_BY_YEAR.
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Holiday:
    name: str
    short_name: str
    kind: str = "national"

    @property
    def label(self):
        return "No laborable" if self.kind == "tourism" else "Feriado"

    @property
    def description(self):
        return f"{self.label}: {self.name}"


HOLIDAYS_BY_YEAR = {
    2026: {
        (1, 1): Holiday("Año Nuevo", "Año Nuevo"),
        (2, 16): Holiday("Carnaval", "Carnaval"),
        (2, 17): Holiday("Carnaval", "Carnaval"),
        (3, 23): Holiday("Día no laborable con fines turísticos", "Día turístico", "tourism"),
        (3, 24): Holiday("Día Nacional de la Memoria por la Verdad y la Justicia", "Memoria y Justicia"),
        (4, 2): Holiday("Día del Veterano y de los Caídos en la Guerra de Malvinas", "Malvinas"),
        (4, 3): Holiday("Viernes Santo", "Viernes Santo"),
        (5, 1): Holiday("Día del Trabajador", "Día del Trabajador"),
        (5, 25): Holiday("Día de la Revolución de Mayo", "Revolución de Mayo"),
        (6, 15): Holiday("Paso a la Inmortalidad del General Martín Miguel de Güemes (trasladado del 17/6)", "Güemes", "transferred"),
        (6, 20): Holiday("Paso a la Inmortalidad del General Manuel Belgrano", "Manuel Belgrano"),
        (7, 9): Holiday("Día de la Independencia", "Independencia"),
        (7, 10): Holiday("Día no laborable con fines turísticos", "Día turístico", "tourism"),
        (8, 17): Holiday("Paso a la Inmortalidad del General José de San Martín", "San Martín"),
        (10, 12): Holiday("Día del Respeto a la Diversidad Cultural", "Diversidad Cultural"),
        (11, 9): Holiday("Visita de Su Santidad el Papa León XIV", "Visita del Papa"),
        (11, 23): Holiday("Día de la Soberanía Nacional (trasladado del 20/11)", "Soberanía Nacional", "transferred"),
        (12, 7): Holiday("Día no laborable con fines turísticos", "Día turístico", "tourism"),
        (12, 8): Holiday("Inmaculada Concepción de María", "Inmaculada Concepción"),
        (12, 25): Holiday("Navidad", "Navidad"),
    },
}


def holiday_for(day: date):
    return HOLIDAYS_BY_YEAR.get(day.year, {}).get((day.month, day.day))
