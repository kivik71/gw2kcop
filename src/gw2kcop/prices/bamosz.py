"""Fund price history exported by hand from BAMOSZ (www.bamosz.hu), CSV.

The site must not be fetched automatically (robots.txt), so the owner downloads the file
and imports it. Layout (checked on a 2026-11 export): quoted values, comma separated,
Windows-1250/ISO-8859-2 text; metadata lines ``"label:","value"``; then a table whose
header starts with ``Dátum, Árfolyam`` and whose rows are newest first, dates
``RRRR/MM/DD`` and prices with a decimal comma. Rows without a price are skipped. The
file has no ISIN, so the caller says which ticker it is.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from gw2kcop.io.text import read_text
from gw2kcop.prices.store import CODE_RE, PriceError

SOURCE = "bamosz"
_CURRENCY_LABEL = "Befektetési jegy devizaneme:"
_NAME_LABEL = "Alap neve:"
_SERIES_LABEL = "Befektetési sorozat megjelölése:"
_TABLE_HEAD = ("Dátum", "Árfolyam")
_DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
_PRICE_RE = re.compile(r"^\d+(,\d+)?$")


@dataclass
class FundHistory:
    name: str
    series: str
    currency: str
    rows: list[tuple[date, Decimal]]


def read_history(path: Path) -> FundHistory:
    lines = list(csv.reader(io.StringIO(read_text(path).text, newline="")))
    meta: dict[str, str] = {}
    start = None
    for n, line in enumerate(lines):
        if tuple(line[:2]) == _TABLE_HEAD:
            start = n + 1
            break
        if len(line) >= 2:
            meta[line[0].strip()] = line[1].strip()
    if start is None:
        raise PriceError(f"{path.name}: no table with columns {', '.join(_TABLE_HEAD)}; not a BAMOSZ export?")
    currency = meta.get(_CURRENCY_LABEL, "")
    if not CODE_RE.match(currency):
        raise PriceError(f"{path.name}: currency line {_CURRENCY_LABEL!r} missing or not like EUR")
    rows: list[tuple[date, Decimal]] = []
    seen: set[date] = set()
    for n, line in enumerate(lines[start:], start=start + 1):
        if not any(cell.strip() for cell in line):
            continue
        where = f"{path.name}:{n}"
        m = _DATE_RE.match(line[0].strip())
        if not m:
            raise PriceError(f"{where}: date {line[0]!r} is not RRRR/MM/DD")
        day = date(int(m[1]), int(m[2]), int(m[3]))
        text = line[1].strip() if len(line) > 1 else ""
        if not text:
            continue  # no price published that day
        if not _PRICE_RE.match(text):
            raise PriceError(f"{where}: price {text!r} is not a number like 12,501183")
        price = Decimal(text.replace(",", "."))
        if not price > 0 or day in seen:
            raise PriceError(f"{where}: price must be > 0 and each date appear once")
        seen.add(day)
        rows.append((day, price))
    return FundHistory(meta.get(_NAME_LABEL, ""), meta.get(_SERIES_LABEL, ""), currency, sorted(rows))
