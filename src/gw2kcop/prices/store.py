"""``<data>/prices.csv``: daily prices per instrument.

Columns (approved 2026-11-01): ``date, ticker, price, currency, source``.
``date`` is the real date of the price (funds publish with a delay); a report uses the
last price on or before its day. ``ticker`` is a ticker from instruments.csv,
``currency`` an ISO code (LSE prices in pence are stored in GBP), ``source`` names where
the price came from (``yahoo``, ``bamosz``, ...).

The key is (date, ticker). A known key with the same price is skipped; a different price
is an error and nothing is written (fail closed).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from gw2kcop.io.text import read_text
from gw2kcop.transactions.master import (  # shared safe-write helpers
    _atomic_write,
    _backup,
    _render,
)

COLUMNS = ("date", "ticker", "price", "currency", "source")
FILE_NAME = "prices.csv"
CODE_RE = re.compile(r"^[A-Z]{3}$")


class PriceError(ValueError):
    """Download, file or merge problem; nothing is written for the affected part."""


@dataclass(frozen=True)
class Price:
    date: date
    ticker: str
    price: Decimal
    currency: str
    source: str

    @property
    def key(self) -> tuple[date, str]:
        return (self.date, self.ticker)


def read_prices(path: Path) -> dict[tuple[date, str], Price]:
    if not path.exists():
        return {}
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise PriceError(f"{path.name}: header must be {','.join(COLUMNS)}")
    out: dict[tuple[date, str], Price] = {}
    for row in reader:
        where = f"{path.name}:{reader.line_num}"
        try:
            p = Price(date.fromisoformat(row["date"]), row["ticker"], Decimal(row["price"]),
                      row["currency"], row["source"])
        except (ValueError, InvalidOperation, TypeError):
            raise PriceError(f"{where}: unreadable row") from None
        if not p.ticker or not CODE_RE.match(p.currency) or not p.price > 0:
            raise PriceError(f"{where}: needs a ticker, a currency like EUR and a price > 0")
        out[p.key] = p
    return out


def merge(path: Path, prices: list[Price], dry_run: bool = False) -> tuple[int, int]:
    """Add new prices; returns ``(added, known)``. A changed price raises ``PriceError``."""
    existing = read_prices(path)
    added: dict[tuple[date, str], Price] = {}
    known = 0
    changed: list[str] = []
    for p in prices:
        old = existing.get(p.key) or added.get(p.key)
        if old is None:
            added[p.key] = p
        elif old.price == p.price and old.currency == p.currency:
            known += 1
        else:
            changed.append(f"{p.ticker} {p.date}: {FILE_NAME} has {old.price} {old.currency} ({old.source}), "
                           f"{p.source} now {p.price} {p.currency}")
    if changed:
        more = f" (and {len(changed) - 5} more)" if len(changed) > 5 else ""
        raise PriceError("changed prices, nothing written:\n  " + "\n  ".join(changed[:5]) + more)
    if added and not dry_run:
        rows = sorted({**existing, **added}.values(), key=lambda r: r.key)
        _backup(path, datetime.now(timezone.utc))
        _atomic_write(path, _render(COLUMNS, ({
            "date": r.date.isoformat(), "ticker": r.ticker, "price": format(r.price, "f"),
            "currency": r.currency, "source": r.source} for r in rows)))
    return len(added), known


def last_known(path: Path) -> dict[str, date]:
    """Latest date per ticker already in the file."""
    out: dict[str, date] = {}
    for (day, ticker) in read_prices(path):
        out[ticker] = max(out.get(ticker, day), day)
    return out
