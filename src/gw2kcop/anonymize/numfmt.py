"""Scale a number written as text, keeping exactly the way it is written."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

_CACHE: dict[str, re.Pattern[str]] = {}


def _pattern(decimal_sep: str) -> re.Pattern[str]:
    if decimal_sep not in _CACHE:
        groups = "., '   ".replace(decimal_sep, "")
        g = re.escape(groups)
        d = re.escape(decimal_sep)
        _CACHE[decimal_sep] = re.compile(
            rf"^(?P<pre>\s*(?:[A-Z]{{3}}\s*|zł\s*|€\s*)?)"
            rf"(?P<sign>[+\-−]?\s*)"
            rf"(?P<int>\d{{1,3}}(?:(?P<g>[{g}])\d{{3}})(?:(?P=g)\d{{3}})*|\d+)"
            rf"(?:{d}(?P<frac>\d+))?"
            rf"(?P<post>\s*(?:[A-Z]{{3}}|zł|€)?\s*)$"
        )
    return _CACHE[decimal_sep]


def has_decimals(text: str, decimal_sep: str) -> bool:
    m = _pattern(decimal_sep).match(text)
    return bool(m and m.group("frac"))


def is_number(text: str, decimal_sep: str) -> bool:
    return bool(text.strip()) and _pattern(decimal_sep).match(text) is not None


def scale_text(text: str, factor: Decimal, decimal_sep: str) -> str | None:
    """Return ``text`` with its number multiplied by ``factor``, or None if not a number.

    Kept: currency prefix/suffix, sign style (``+ ``), thousands separator, number
    of decimal places. Rounding is half-up to the original precision.
    """
    m = _pattern(decimal_sep).match(text)
    if not m:
        return None
    frac = m.group("frac") or ""
    raw = Decimal(m.group("int").replace(m.group("g") or "\x00", "") + ("." + frac if frac else ""))
    scaled = (raw * factor).quantize(Decimal(1).scaleb(-len(frac)), rounding=ROUND_HALF_UP)
    int_part, _, frac_part = f"{scaled:f}".partition(".")
    sep = m.group("g")
    if sep:
        head = len(int_part) % 3 or 3
        chunks = [int_part[:head]] + [int_part[i:i + 3] for i in range(head, len(int_part), 3)]
        int_part = sep.join(chunks)
    number = int_part + (decimal_sep + frac_part if frac else "")
    return m.group("pre") + m.group("sign") + number + m.group("post")
