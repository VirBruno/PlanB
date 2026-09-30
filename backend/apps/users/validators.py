"""Reglas compartidas de formularios; la política de password coincide con Auth."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


validate_username = RegexValidator(
    regex=r"\A[A-Za-z0-9][A-Za-z0-9_.-]{2,29}\Z",
    message=(
        "Usá entre 3 y 30 caracteres: letras sin acentos, números, punto, guion "
        "o guion bajo. Empezá con una letra o un número."
    ),
    code="invalid_username",
)


def validate_password(value):
    """Mínimo 12 caracteres y grupos ASCII equivalentes a letters_digits."""
    if len(value) < 12:
        raise ValidationError("Usá al menos 12 caracteres.", code="password_too_short")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"[0-9]", value):
        raise ValidationError(
            "Incluí al menos una letra (a-z o A-Z) y un número (0-9).",
            code="password_letters_digits",
        )
