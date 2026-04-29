from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, {})


@register.filter
def split(value, delimiter=','):
    return value.split(delimiter)


@register.filter
def peso(value):
    """
    Format a number as Philippine Peso with commas.
    e.g. 35699.00 → ₱35,699.00
    Usage in template: {{ value|peso }}
    """
    try:
        value = Decimal(str(value))
        return '₱{:,.2f}'.format(value)
    except (TypeError, ValueError, InvalidOperation):
        return '—'