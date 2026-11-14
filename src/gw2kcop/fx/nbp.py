"""NBP average exchange rates (table A) -> ``<data>/fx_rates.csv``.

Source: the official NBP API, ``https://api.nbp.pl/api/exchangerates/rates/A/{code}/{start}/{end}/``
(free, no key). Only days with a published table get a row; for a day without one
(weekend, holiday) the reader uses the last earlier rate. Table A is published since
2002-01-02 and one request may cover at most 367 days.

``fx_rates.csv`` columns (approved 2026-11-01): ``date, currency, rate_pln, nbp_table``.
A known (date, currency) with the same rate is skipped; a different rate is an error and
nothing is written (fail closed).
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from gw2kcop.io.text import read_text
from gw2kcop.transactions.master import _atomic_write, _backup, _render  # shared safe-write helpers

HOST = "api.nbp.pl"  # the only host this module talks to
URL = "https://" + HOST + "/api/exchangerates/rates/A/{code}/{start}/{end}/?format=json"
FIRST_DAY = date(2002, 1, 2)  # first table A in the API
MAX_DAYS = 367
TIMEOUT = 30
RETRIES = 3
COLUMNS = ("date", "currency", "rate_pln", "nbp_table")
FILE_NAME = "fx_rates.csv"
_CODE_RE = re.compile(r"^[A-Z]{3}$")
_TABLE_RE = re.compile(r"^\d{3}/A/NBP/\d{4}$")


class FxError(ValueError):
    """Download, validation or merge problem; nothing is written."""


@dataclass(frozen=True)
class Rate:
    date: date
    currency: str
    rate_pln: Decimal
    nbp_table: str


Fetch = Callable[[str], bytes | None]  # URL -> body, or None for "no data" (HTTP 404)


def http_fetch(url: str) -> bytes | None:
    """GET with certificate checks (default), a timeout and bounded retries."""
    if not url.startswith("https://" + HOST + "/"):
        raise FxError(f"refusing to contact {url!r}: only {HOST} is allowed")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "gw2kcop"})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (fixed https host)
                return response.read(5_000_000)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # NBP: no table in the whole range
                return None
            if exc.code < 500 or attempt == RETRIES - 1:
                raise FxError(f"NBP API: HTTP {exc.code} for {url}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == RETRIES - 1:
                raise FxError(f"NBP API not reachable: {getattr(exc, 'reason', exc)}") from None
        time.sleep(2 ** attempt)
    raise FxError("NBP API: no response")  # pragma: no cover


def _chunks(start: date, end: date):
    while start <= end:
        stop = min(end, start + timedelta(days=MAX_DAYS - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def download(code: str, start: date, end: date, fetch: Fetch | None = None) -> list[Rate]:
    """All table A rates of ``code`` between ``start`` and ``end``, validated."""
    fetch = fetch or http_fetch
    if not _CODE_RE.match(code) or code == "PLN":
        raise FxError(f"not a foreign currency code: {code!r}")
    start = max(start, FIRST_DAY)
    out: list[Rate] = []
    for a, b in _chunks(start, end):
        body = fetch(URL.format(code=code.lower(), start=a.isoformat(), end=b.isoformat()))
        if body is None:
            continue
        try:
            data = json.loads(body, parse_float=Decimal)
            if data.get("table") != "A" or data.get("code", "").upper() != code:
                raise FxError(f"NBP API: answer for {data.get('table')}/{data.get('code')}, asked A/{code}")
            for item in data["rates"]:
                day = date.fromisoformat(item["effectiveDate"])
                mid = Decimal(str(item["mid"]))
                table = item["no"]
                if not a <= day <= b or not mid > 0 or not _TABLE_RE.match(table):
                    raise FxError(f"NBP API: implausible rate {item!r} for {code}")
                out.append(Rate(day, code, mid, table))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, FxError):
                raise
            raise FxError(f"NBP API: unexpected answer for {code} {a}..{b}: {exc}") from None
    return out


def read_rates(path: Path) -> dict[tuple[date, str], Rate]:
    if not path.exists():
        return {}
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise FxError(f"{path.name}: header must be {','.join(COLUMNS)}")
    out: dict[tuple[date, str], Rate] = {}
    for row in reader:
        rate = Rate(date.fromisoformat(row["date"]), row["currency"], Decimal(row["rate_pln"]), row["nbp_table"])
        out[(rate.date, rate.currency)] = rate
    return out


def merge(path: Path, rates: list[Rate], dry_run: bool = False) -> tuple[int, int]:
    """Add new rates; returns ``(added, known)``. A changed rate raises ``FxError``."""
    existing = read_rates(path)
    added: dict[tuple[date, str], Rate] = {}
    known = 0
    for rate in rates:
        key = (rate.date, rate.currency)
        old = existing.get(key) or added.get(key)
        if old is None:
            added[key] = rate
        elif old.rate_pln == rate.rate_pln:
            known += 1
        else:
            raise FxError(f"{rate.currency} {rate.date}: {FILE_NAME} has {old.rate_pln}, NBP now {rate.rate_pln}; "
                          "nothing written")
    if added and not dry_run:
        rows = sorted({**existing, **added}.values(), key=lambda r: (r.date, r.currency))
        _backup(path, datetime.now(timezone.utc))
        _atomic_write(path, _render(COLUMNS, ({
            "date": r.date.isoformat(), "currency": r.currency,
            "rate_pln": format(r.rate_pln, "f"), "nbp_table": r.nbp_table} for r in rows)))
    return len(added), known


def last_known(path: Path) -> dict[str, date]:
    """Latest date per currency already in the file."""
    out: dict[str, date] = {}
    for (day, code) in read_rates(path):
        out[code] = max(out.get(code, day), day)
    return out


def wanted_currencies(data_dir: Path) -> set[str]:
    """Foreign currencies used in transactions.csv (amount or price) or already in fx_rates.csv."""
    from gw2kcop.transactions.master import read_master

    codes = {code for _, code in read_rates(data_dir / FILE_NAME)}
    for row in read_master(data_dir / "transactions.csv"):
        codes.update(c for c in (row["currency"], row["price_currency"]) if c)
    return {c for c in codes if c != "PLN"}


def first_transaction_date(data_dir: Path) -> date | None:
    from gw2kcop.transactions.master import read_master

    days = [date.fromisoformat(r["date"]) for r in read_master(data_dir / "transactions.csv")]
    return min(days) if days else None
