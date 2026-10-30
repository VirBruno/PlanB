from django import template

register = template.Library()


@register.filter
def expense_money(value):
    # Decimal.__format__, sin conversión intermedia a float ni dependencias locales.
    return "$" + format(value, ",.2f").translate(str.maketrans({",": ".", ".": ","}))


@register.filter
def expense_balance(value):
    return ("+" if value > 0 else "-" if value < 0 else "") + expense_money(abs(value))
