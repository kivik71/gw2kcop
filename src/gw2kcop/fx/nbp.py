"""NBP average exchange rates (table A), base currency PLN.

Source: the official NBP API, ``https://api.nbp.pl/api/exchangerates/rates/A/{code}/{start}/{end}/``
(free, no key). Table A is published since 2002-01-02 and one request may cover at most
367 days. Rates are returned as :class:`gw2kcop.fx.rates.Rate` with ``base_currency=PLN``
and ``source=NBP``; storing them is the job of :mod:`gw2kcop.fx.rates`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from gw2kcop.fx.rates import CODE_RE, FxError, Rate

SOURCE = "NBP"
BASE_CURRENCY = "PLN"
HOST = "api.nbp.pl"  # the only host this module talks to
URL = "https://" + HOST + "/api/exchangerates/rates/A/{code}/{start}/{end}/?format=json"
FIRST_DAY = date(2002, 1, 2)  # first table A in the API
MAX_DAYS = 367
TIMEOUT = 30
RETRIES = 3
# Older tables are not zero-padded (e.g. "1/A/NBP/2009"), newer ones are ("001/A/NBP/2026").
_TABLE_RE = re.compile(r"^\d{1,3}/A/NBP/\d{4}$")


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
    if not CODE_RE.match(code) or code == BASE_CURRENCY:
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
                out.append(Rate(day, BASE_CURRENCY, code, mid, SOURCE, table))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, FxError):
                raise
            raise FxError(f"NBP API: unexpected answer for {code} {a}..{b}: {exc}") from None
    return out
