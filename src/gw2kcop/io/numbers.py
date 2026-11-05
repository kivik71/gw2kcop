"""Parse numbers as written in Polish/EU exports into ``Decimal``.

The decimal separator is always given by the caller (from the format's TOML), never
guessed: ``1.165`` means 1165 in a PL export and 1.165 in an EN one.

Accepted:
- optional sign (``-``, ``+``, or Unicode minus), possibly followed by a space
- thousands separators: space, no-break spaces, apostrophe, and the other of ``.``/``,``
  (only in groups of three digits)
- an optional currency symbol or code before or after the number
"""

from __future__ import annotations

import re
from decimal import Decimal

_SPACES = "\u00a0\u202f\u2009 "  # no-break, narrow no-break, thin, normal space
_CURRENCY_RE = re.compile(r"^(?:[A-Z]{3}|z\u0142|\u20ac|\$|\u00a3)\s*|\s*(?:[A-Z]{3}|z\u0142|\u20ac|\$|\u00a3)$")
_EMPTY = {"", "-", "\u2013", "\u2014"}  # empty, hyphen, en dash, em dash


def _number_re(decimal_sep: str) -> re.Pattern[str]:
    thousands = "." if decimal_sep == "," else ","
    dec = re.escape(decimal_sep)
    # One thousands separator per number, used consistently, groups of three digits.
    grouped = rf"\d{{1,3}}(?P<g>[{re.escape(thousands)}' ])\d{{3}}(?:(?P=g)\d{{3}})*"
    return re.compile(rf"^(?:{grouped}|\d+)(?:{dec}\d+)?$|^{dec}\d+$")


_PATTERNS = {",": _number_re(","), ".": _number_re(".")}


def parse_decimal(text: str, decimal_sep: str = ",") -> Decimal:
    """Parse ``text`` into ``Decimal``; raise ``ValueError`` on anything unexpected."""
    if decimal_sep not in _PATTERNS:
        raise ValueError(f"unsupported decimal separator: {decimal_sep!r}")
    raw = text
    value = text.strip()
    value = _CURRENCY_RE.sub("", value).strip()
    sign = ""
    if value and value[0] in "+-\u2212":
        sign = "-" if value[0] in "-\u2212" else ""
        value = value[1:].lstrip(_SPACES)
    for space in _SPACES:
        value = value.replace(space, " ")
    if not value or not _PATTERNS[decimal_sep].match(value):
        raise ValueError(f"not a number: {raw!r}")
    thousands = "." if decimal_sep == "," else ","
    for sep in (" ", "'", thousands):
        value = value.replace(sep, "")
    value = value.replace(decimal_sep, ".")
    return Decimal(sign + value)


def parse_optional_decimal(text: str | None, decimal_sep: str = ",") -> Decimal | None:
    """Like ``parse_decimal`` but return ``None`` for empty cells and a lone dash."""
    if text is None or text.strip() in _EMPTY:
        return None
    return parse_decimal(text, decimal_sep)
