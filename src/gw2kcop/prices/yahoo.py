"""Optional Yahoo Finance adapter (``pip install gw2kcop[prices-yahoo]``).

Uses the third-party ``yfinance`` package: an unofficial interface to Yahoo, for personal
use only, which can break without notice. The symbol is ``source_symbol`` from
instruments.csv (e.g. ``PKN.WA``, ``EUNL.DE``, ``AIAG.L``). Closing prices are not
adjusted for dividends. Prices in pence (``GBp``/``GBX``) are converted to GBP.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from gw2kcop.prices.store import CODE_RE, PriceError

SOURCE = "yahoo"
_PENCE = {"GBp", "GBX"}
_DECIMALS = Decimal("0.000001")


def _module():
    try:
        import yfinance  # optional dependency, imported only when used
    except ImportError:
        raise PriceError("Yahoo prices need the optional package yfinance: "
                         "python -m pip install yfinance") from None
    return yfinance


def fetch(symbol: str, start: date, end: date, yf=None) -> tuple[str, list[tuple[date, Decimal]]]:
    """Daily closes of ``symbol`` from ``start`` to ``end`` (inclusive): ``(currency, rows)``."""
    yf = yf or _module()
    try:
        ticker = yf.Ticker(symbol)
        frame = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                               interval="1d", auto_adjust=False, actions=False)
        meta = getattr(ticker, "history_metadata", None) or {}
        currency = meta.get("currency") or ticker.fast_info["currency"]
    except PriceError:
        raise
    except Exception as exc:  # noqa: BLE001 - the package raises many kinds of errors; report, do not crash
        raise PriceError(f"Yahoo {symbol}: {type(exc).__name__}: {exc}") from None
    if frame is None or len(frame) == 0:
        return str(currency or ""), []
    divisor = Decimal(1)
    if currency in _PENCE:
        currency, divisor = "GBP", Decimal(100)
    if not isinstance(currency, str) or not CODE_RE.match(currency):
        raise PriceError(f"Yahoo {symbol}: unexpected currency {currency!r}")
    rows: list[tuple[date, Decimal]] = []
    for stamp, close in zip(frame.index, frame["Close"]):
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        day = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
        price = (Decimal(repr(float(close))) / divisor).quantize(_DECIMALS)
        if not start <= day <= end or not price > 0:
            raise PriceError(f"Yahoo {symbol}: implausible close {close!r} on {day}")
        rows.append((day, price))
    return currency, rows
