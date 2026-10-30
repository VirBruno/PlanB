"""Selección puramente visual: el mismo UUID conserva su símbolo en cualquier vista."""
from uuid import UUID

from django import template

register = template.Library()


@register.filter
def group_variant(value):
    try:
        return UUID(str(value)).int % 3 + 1
    except (ValueError, TypeError, AttributeError):
        return 1
