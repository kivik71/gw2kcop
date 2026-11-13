"""Broker valuation history -> ``broker_valuations.csv`` (date, account_id, value_pln, source_file).

Only exports that state a total account value per day (today: mBank "Historia wycen").
``Kontrola`` compares these numbers with our own computed value; they never replace it.

Rules:
- A row with a time other than 00:00:00 is the value at the moment of export (the day is
  not over yet), so it is not stored; the next export brings that day's final value.
- One value per account and date. The same value again is "already known"; a different
  value for a stored date stops the file (fail closed) until the owner decides.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from gw2kcop.accounts import Account
from gw2kcop.io.dates import DATE_FORMATS, parse_date, parse_datetime
from gw2kcop.io.numbers import parse_decimal
from gw2kcop.io.text import read_text
from gw2kcop.transactions import master
from gw2kcop.transactions.engine import MAX_ERRORS, ParseError, read_rows
from gw2kcop.transactions.formats import FormatSpec

FILE_NAME = "broker_valuations.csv"
COLUMNS = ("date", "account_id", "value_pln", "source_file")


@dataclass(frozen=True)
class Valuation:
    date: date
    account_id: str
    value_pln: Decimal


@dataclass
class ValuationFile:
    rows: list[Valuation] = field(default_factory=list)
    intraday_skipped: int = 0


def parse_valuations(text: str, spec: FormatSpec, account: Account) -> ValuationFile:
    """Parse one valuation export. Raises ``ParseError`` listing every problem found."""
    cols = spec.columns
    out = ValuationFile()
    errors: list[str] = []
    seen: dict[date, int] = {}
    for line, row, _raw in read_rows(text, spec):
        text_date, text_value = row[cols["date"]], row[cols["value"]]
        try:
            if spec.datetime_format in DATE_FORMATS:
                day, midnight = parse_date(text_date, spec.datetime_format), True
            else:
                moment = parse_datetime(text_date, spec.datetime_format, spec.timezone)
                day, midnight = moment.date(), (moment.hour, moment.minute, moment.second) == (0, 0, 0)
        except ValueError:
            errors.append(f"line {line}: date {text_date!r} does not match {spec.datetime_format}")
            continue
        try:
            value = parse_decimal(text_value, spec.decimal)
        except ValueError:
            errors.append(f"line {line}: not a number: {text_value!r}")
            continue
        if value < 0:
            errors.append(f"line {line}: negative valuation {value}")
            continue
        if not midnight:
            out.intraday_skipped += 1
            continue
        if day in seen:
            errors.append(f"line {line}: date {day} repeats line {seen[day]}")
            continue
        seen[day] = line
        out.rows.append(Valuation(day, account.account_id, value))
    if errors:
        raise ParseError(errors[:MAX_ERRORS], [])
    return out


def read_valuations(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise master.TransactionsError(f"{path.name}: header does not match the approved schema")
    return list(reader)


def _plan(data_dir: Path, rows: list[Valuation]) -> tuple[list[dict[str, str]], list[Valuation], int]:
    """``(existing rows, rows to add, already known)``; raises on a changed value."""
    existing = read_valuations(data_dir / FILE_NAME)
    stored = {(r["account_id"], r["date"]): Decimal(r["value_pln"]) for r in existing}
    new: list[Valuation] = []
    known = 0
    conflicts: list[str] = []
    for v in rows:
        old = stored.get((v.account_id, v.date.isoformat()))
        if old is None:
            new.append(v)
        elif old == v.value_pln:
            known += 1
        else:
            conflicts.append(f"{v.account_id} {v.date}: stored {master.format_decimal(old)}, "
                             f"export {master.format_decimal(v.value_pln)}")
    if conflicts:
        more = f" (+{len(conflicts) - 5} more)" if len(conflicts) > 5 else ""
        raise master.TransactionsError("the broker changed values already stored: "
                                       + "; ".join(conflicts[:5]) + more)
    return existing, new, known


def count_new(data_dir: Path, rows: list[Valuation]) -> tuple[int, int]:
    _, new, known = _plan(data_dir, rows)
    return len(new), known


def merge(data_dir: Path, rows: list[Valuation], source_file: str,
          now: datetime | None = None) -> tuple[int, int]:
    """Add new valuation rows; ``(added, already known)``. Nothing is written on a conflict."""
    now = now or datetime.now(timezone.utc)
    path = data_dir / FILE_NAME
    existing, new, known = _plan(data_dir, rows)
    if new:
        added = [{"date": v.date.isoformat(), "account_id": v.account_id,
                  "value_pln": master.format_decimal(v.value_pln),
                  "source_file": master.neutralize(source_file)} for v in new]
        merged = sorted([*existing, *added], key=lambda r: (r["account_id"], r["date"]))
        master._backup(path, now)
        master._atomic_write(path, master._render(COLUMNS, merged))
    master.append_log(data_dir, {
        "imported_at": master.format_utc(now), "source_file": source_file,
        "account_id": ",".join(sorted({v.account_id for v in rows})), "rows_in": str(len(rows)),
        "added": str(len(new)), "skipped": str(known), "status": "ok", "message": FILE_NAME,
    })
    return len(new), known
