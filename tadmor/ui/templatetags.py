"""Template filters for exact amounts (domain §13 G7)."""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def amount(v):
    """An exact decimal, grouped, with at least two places: "1,234.50", "10.0011"."""
    if v is None or v == "":
        return ""
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return v
    s = f"{d:f}"
    sign = "-" if s.startswith("-") else ""
    whole, _, frac = s.lstrip("-").partition(".")
    frac = frac.rstrip("0").ljust(2, "0")
    return f"{sign}{int(whole):,}.{frac}"


@register.filter
def qty(v):
    """A quantity or rate without trailing zeros: "1.5", "3"."""
    if v is None or v == "":
        return ""
    s = f"{Decimal(str(v)):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


@register.filter
def is_negative(v):
    try:
        return Decimal(str(v)) < 0
    except (InvalidOperation, TypeError):
        return False


@register.filter
def get(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.filter
def label(v):
    """A status or code word made readable: "partial" -> "Partial", "transfer_in" -> "Transfer in"."""
    return str(v).replace("_", " ").capitalize() if v else ""
