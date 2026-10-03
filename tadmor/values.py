"""Exact decimal and date values, and typed access to request bodies.

Decimals arrive as strings and are rounded half away from zero to their
stored scale before anything checks or uses them (spec/api.md §1.2).
Python's Decimal does this exactly; nothing passes through binary floating
point.
"""

import datetime
import decimal
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import BadRequest, Unprocessable

# (scale, magnitude limit) per kind of decimal, spec/api.md §1.2.
MONEY = (4, Decimal(10) ** 15)
RATE = (4, Decimal(1000))  # a tax rate in percent
FX = (8, Decimal(10) ** 11)  # an exchange rate

_DECIMAL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Products of two stored values (19 digits each) must be exact before they
# are rounded, so every thread computes with ample precision.
decimal.DefaultContext.prec = 80
decimal.getcontext().prec = 80

Q4 = Decimal("0.0001")
ZERO = Decimal(0)


def round4(d):
    return d.quantize(Q4, rounding=ROUND_HALF_UP)


def parse_decimal(text, kind=MONEY, name="value"):
    """Parse a plain decimal string, rounded to the kind's scale."""
    text = text.strip()
    if not _DECIMAL.fullmatch(text):
        raise Unprocessable(f"{name} must be a decimal number")
    scale, limit = kind
    try:
        d = Decimal(text).quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise Unprocessable(f"{name} is out of range")
    if abs(d) >= limit:
        raise Unprocessable(f"{name} is out of range")
    return d


def check_magnitude(d, name="amount"):
    """Refuse a computed amount at or beyond the money limit."""
    if abs(d) >= MONEY[1]:
        raise Unprocessable(f"{name} is out of range")
    return d


def parse_date(text, name="date", status=Unprocessable):
    if not _DATE.fullmatch(text):
        raise status(f"{name} must be a YYYY-MM-DD date")
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise status(f"{name} must be a valid YYYY-MM-DD date")


def fmt4(d):
    """Money and quantities: scale 4, e.g. "9.9900"."""
    if d is None:
        return None
    return f"{round4(Decimal(d)):f}"


def fmt_rate(d):
    """Exchange rates: trailing zeros trimmed, e.g. "1.125"."""
    if d is None:
        return None
    s = f"{Decimal(d):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def fmt_date(d):
    return d.isoformat() if d is not None else None


def today():
    """Today's date in UTC, whatever the server's timezone (spec/api.md §1.2)."""
    return datetime.datetime.now(datetime.UTC).date()


def positive_int(text, name="id"):
    """A path id or similar: a positive integer, else 400."""
    if not text.isdigit() or int(text) <= 0 or len(text) > 9:
        raise BadRequest(f"invalid {name}")
    return int(text)


class Body:
    """Typed read access to a JSON object from a request.

    A value of the wrong JSON type is a 400 (the request cannot be
    interpreted). A string that does not hold a valid decimal or date is a
    422 (spec/api.md §1.4). Absent and null are the same thing.
    """

    def __init__(self, data):
        if not isinstance(data, dict):
            raise BadRequest("request body must be a JSON object")
        self.data = data

    def has(self, name):
        return self.data.get(name) is not None

    def raw(self, name):
        return self.data.get(name)

    def str(self, name):
        v = self.data.get(name)
        if v is not None and not isinstance(v, str):
            raise BadRequest(f"{name} must be a string")
        return v

    def text(self, name):
        """An optional text field: absent, null, and "" all mean null."""
        v = self.str(name)
        return v if v else None

    def required_str(self, name):
        v = self.str(name)
        if not v:
            raise BadRequest(f"{name} is required")
        return v

    def int(self, name):
        v = self.data.get(name)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise BadRequest(f"{name} must be an integer")
        return v

    def required_id(self, name):
        v = self.int(name)
        if v is None or v <= 0:
            raise BadRequest(f"{name} is required")
        return v

    def bool(self, name):
        v = self.data.get(name)
        if v is None:
            return False
        if not isinstance(v, bool):
            raise BadRequest(f"{name} must be a boolean")
        return v

    def decimal(self, name, kind=MONEY, default=None):
        v = self.str(name)
        if v is None or v == "":
            return default
        return parse_decimal(v, kind, name)

    def date(self, name):
        v = self.str(name)
        if not v:
            return None
        return parse_date(v, name)

    def list(self, name):
        v = self.data.get(name)
        if v is None:
            return []
        if not isinstance(v, list):
            raise BadRequest(f"{name} must be an array")
        return [Body(item) for item in v]
