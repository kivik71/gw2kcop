"""``<data>/fx_rates.csv``: daily exchange rates, independent of the source.

Columns (approved 2026-11-14): ``date, base_currency, currency, rate, source, table``.
One row means: on ``date`` one unit of ``currency`` cost ``rate`` units of
``base_currency``, as published by ``source`` (e.g. ``NBP``) in ``table`` (the source's own
table id, may be empty). Only days with a published rate get a row; for a day without one
(weekend, holiday) the reader uses the last earlier rate.

The key is (date, base_currency, currency). A known key with the same rate is skipped; a
different rate is an error and nothing is written (fail closed).
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

COLUMNS = ("date", "base_currency", "currency", "rate", "source", "table")
FILE_NAME = "fx_rates.csv"
CODE_RE = re.compile(r"^[A-Z]{3}$")


class FxError(ValueError):
    """Download, validation or merge problem; nothing is written."""


@dataclass(frozen=True)
class Rate:
    date: date
    base_currency: str
    currency: str
    rate: Decimal
    source: str
    table: str = ""

    @property
    def key(self) -> tuple[date, str, str]:
        return (self.date, self.base_currency, self.currency)


Key = tuple[date, str, str]


def read_rates(path: Path) -> dict[Key, Rate]:
    if not path.exists():
        return {}
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise FxError(f"{path.name}: header must be {','.join(COLUMNS)}")
    out: dict[Key, Rate] = {}
    for row in reader:
        where = f"{path.name}:{reader.line_num}"
        try:
            rate = Rate(date.fromisoformat(row["date"]), row["base_currency"], row["currency"],
                        Decimal(row["rate"]), row["source"], row["table"])
        except (ValueError, InvalidOperation, TypeError):
            raise FxError(f"{where}: unreadable row") from None
        if not (CODE_RE.match(rate.base_currency) and CODE_RE.match(rate.currency)) or not rate.rate > 0:
            raise FxError(f"{where}: currency codes must be like USD and the rate > 0")
        out[rate.key] = rate
    return out


def merge(path: Path, rates: list[Rate], dry_run: bool = False) -> tuple[int, int]:
    """Add new rates; returns ``(added, known)``. A changed rate raises ``FxError``."""
    existing = read_rates(path)
    added: dict[Key, Rate] = {}
    known = 0
    for rate in rates:
        old = existing.get(rate.key) or added.get(rate.key)
        if old is None:
            added[rate.key] = rate
        elif old.rate == rate.rate:
            known += 1
        else:
            raise FxError(f"{rate.currency}/{rate.base_currency} {rate.date}: {FILE_NAME} has {old.rate} "
                          f"({old.source}), {rate.source} now {rate.rate}; nothing written")
    if added and not dry_run:
        rows = sorted({**existing, **added}.values(), key=lambda r: r.key)
        _backup(path, datetime.now(timezone.utc))
        _atomic_write(path, _render(COLUMNS, ({
            "date": r.date.isoformat(), "base_currency": r.base_currency, "currency": r.currency,
            "rate": format(r.rate, "f"), "source": r.source, "table": r.table} for r in rows)))
    return len(added), known


def last_known(path: Path, base_currency: str) -> dict[str, date]:
    """Latest date per currency already in the file, for one base currency."""
    out: dict[str, date] = {}
    for (day, base, code) in read_rates(path):
        if base == base_currency:
            out[code] = max(out.get(code, day), day)
    return out


def wanted_currencies(data_dir: Path, base_currency: str) -> set[str]:
    """Currencies other than the base used in transactions.csv or already in fx_rates.csv."""
    from gw2kcop.transactions.master import read_master

    codes = {code for _, base, code in read_rates(data_dir / FILE_NAME) if base == base_currency}
    for row in read_master(data_dir / "transactions.csv"):
        codes.update(c for c in (row["currency"], row["price_currency"]) if c)
    return {c for c in codes if c != base_currency}


def first_transaction_date(data_dir: Path) -> date | None:
    from gw2kcop.transactions.master import read_master

    days = [date.fromisoformat(r["date"]) for r in read_master(data_dir / "transactions.csv")]
    return min(days) if days else None
