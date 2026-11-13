"""XTB account statement (.xlsx, one file per currency sub-account): "Cash Operations" sheet.

Format notes: research/formats/xtb-and-bossa-2026-11.md, section 1 (samples 2026-11-03).
Written in code, not TOML (decision 2026-11-04): the file is XLSX with a metadata block,
and quantity and price are only in the free-text ``Comment``.

Fail closed, like the TOML engine:
- the file must be an XTB statement (sheet names, ``Account number`` in A1, exact header);
- the account number in the file must equal ``export_identifier`` when that is set;
- the currency of the "Open Positions" summary must equal the account currency;
- every ``Type`` must be known; types not seen in a sample yet (sale, commission, fee,
  corporate action) stop the file, and so does a row in "Closed Positions";
- a purchase must satisfy quantity x price = amount (0.01), so a fee or a conversion
  inside a purchase cannot pass unnoticed;
- the sum of all amounts must equal the ``Total`` row.
"""

from __future__ import annotations

import re
import warnings
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from gw2kcop.accounts import Account
from gw2kcop.transactions.engine import MAX_ERRORS, ParseError
from gw2kcop.transactions.master import Transaction
from gw2kcop.transactions.reference import Reference

INSTITUTION = "xtb"
SHEETS = ("Closed Positions", "Cash Operations", "Open Positions")
CASH_HEADER = ("Type", "Instrument", "Ticker", "Category", "Time", "Amount", "ID", "Comment",
               "Product", "Position ID")
HEADER_ROW = 5
PRODUCT = "My Trades"
CENT = Decimal("0.01")

_BUY_RE = re.compile(
    r"^OPEN BUY (?:(?P<ticker>[A-Z0-9][A-Z0-9.\-]*\.[A-Z]{2}) )?"
    r"(?P<qty>\d+(?:\.\d+)?)(?:/(?P<total>\d+(?:\.\d+)?))? @ (?P<price>\d+(?:\.\d+)?)$")
_INTEREST_RE = re.compile(r"^Free-funds Interest (?P<period>\d{4}-\d{2})$")
_INTEREST_TAX_RE = re.compile(r"^Free-funds Interest Tax (?P<period>\d{4}-\d{2})$")
_FX_RE = re.compile(r"^Currency conversion, (?P<src>[A-Z]{3}) to (?P<dst>[A-Z]{3}) from TA: ")
# Type -> (our type, sign of Amount: +1 credit, -1 debit, 0 either)
_TYPES = {
    "Deposit": ("deposit", 1),
    "Withdrawal": ("withdrawal", -1),
    "Stock purchase": ("buy", -1),
    "Dividend": ("dividend", 1),
    "Withholding tax": ("tax", -1),
    "Free funds interest": ("interest", 1),
    "Free funds interest tax": ("tax", -1),
    "Transfer": ("fx_exchange", 0),  # currency conversion between own sub-accounts (plan 2.4)
}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _amount(value) -> Decimal:
    """Amounts are numeric cells in cents; a float is turned into an exact 2-decimal value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a number: {value!r}")
    exact = Decimal(f"{value:.2f}")
    if abs(Decimal(repr(value)) - exact) > Decimal("0.000001"):
        raise ValueError(f"more than 2 decimals: {value!r}")
    return exact


def _rows(ws) -> list[tuple]:
    return [tuple(row) for row in ws.iter_rows(values_only=True)]


def _trim(row: tuple) -> tuple:
    cells = list(row)
    while cells and cells[-1] in (None, ""):
        cells.pop()
    return tuple(cells)


def _load(source):
    """``source``: a path, or a binary file object (a member read from a ZIP)."""
    import openpyxl  # optional dependency path: only XTB needs it

    try:
        with warnings.catch_warnings():
            # XTB files have no default cell style; openpyxl warns, the data is unaffected.
            warnings.filterwarnings("ignore", message="Workbook contains no default style")
            return openpyxl.load_workbook(source, data_only=True)
    except Exception as exc:  # zip/xml errors: not an XLSX we can read
        raise ParseError([f"cannot read as .xlsx: {exc}"], []) from None


def _check_account(wb, account: Account, errors: list[str], notes: list[str]) -> None:
    numbers = set()
    for name in SHEETS:
        first = _rows(wb[name])[0] if wb[name].max_row else ()
        if len(first) < 2 or first[0] != "Account number":
            errors.append(f"{name}: A1 is not 'Account number'")
            continue
        numbers.add(_text(first[1]))
    if len(numbers) > 1:
        errors.append("the sheets name different account numbers")
    elif account.export_identifier:
        if numbers and account.export_identifier not in numbers:
            errors.append(f"account number in the file does not match export_identifier of {account.account_id}")
    else:
        notes.append(f"export_identifier of {account.account_id} is empty: account number not checked")


def _check_currency(wb, account: Account, errors: list[str]) -> None:
    for row in _rows(wb["Open Positions"]):
        if len(row) >= 4 and row[1] == "Open position value":
            if _text(row[3]) != account.currency:
                errors.append(f"Open Positions: values in {_text(row[3])!r}, account currency is {account.currency}")
            return


def _check_closed(wb, errors: list[str]) -> None:
    rows = [_trim(r) for r in _rows(wb["Closed Positions"])[HEADER_ROW:]]
    data = [r for r in rows if r and r[0] != "Profit/loss"]
    if data:
        errors.append(f"Closed Positions has {len(data)} row(s): sales are not supported yet "
                      "(send an anonymised sample with a sale)")


def account_number(source) -> str:
    """The account number in A1/B1 of "Cash Operations"; raises ``ParseError``."""
    wb = _load(source)
    if "Cash Operations" not in wb.sheetnames:
        raise ParseError([f"not an XTB statement: sheets {wb.sheetnames}"], [])
    first = _rows(wb["Cash Operations"])[0] if wb["Cash Operations"].max_row else ()
    if len(first) < 2 or first[0] != "Account number" or not _text(first[1]):
        raise ParseError(["Cash Operations: A1 is not 'Account number'"], [])
    return _text(first[1])


def parse_statement(source: Path, account: Account, reference: Reference,
                    local_tz: ZoneInfo) -> tuple[list[Transaction], list[str]]:
    """Parse one XTB statement (a path or a binary file object).

    Returns ``(transactions, notes)``; raises ``ParseError``.
    """
    wb = _load(source)
    if tuple(wb.sheetnames) != SHEETS:
        raise ParseError([f"not an XTB statement: sheets {wb.sheetnames}"], [])
    errors: list[str] = []
    notes: list[str] = []
    missing: set[str] = set()
    _check_account(wb, account, errors, notes)
    _check_currency(wb, account, errors)
    _check_closed(wb, errors)

    rows = _rows(wb["Cash Operations"])
    if len(rows) < HEADER_ROW or _trim(rows[HEADER_ROW - 1]) != CASH_HEADER:
        raise ParseError([*errors, "Cash Operations: header row 5 is not the known XTB header"], [])
    out: list[Transaction] = []
    total_row: tuple | None = None
    total = Decimal(0)
    seen_ids: set[str] = set()
    for n, raw in enumerate(rows[HEADER_ROW:], start=HEADER_ROW + 1):
        row = _trim(raw)
        if not row:
            continue
        if total_row is not None:
            errors.append(f"row {n}: data after the Total row")
            continue
        if row[0] == "Total":
            total_row = raw
            continue
        cells = dict(zip(CASH_HEADER, list(raw) + [None] * len(CASH_HEADER)))
        kind = _text(cells["Type"])
        where = f"row {n}"
        if kind not in _TYPES:
            errors.append(f"{where}: unknown type {kind!r} (not seen in a sample yet)")
            continue
        our_type, sign = _TYPES[kind]
        if _text(cells["Product"]) != PRODUCT:
            errors.append(f"{where}: unknown product {_text(cells['Product'])!r}")
            continue
        moment = cells["Time"]
        if not isinstance(moment, datetime):
            errors.append(f"{where}: Time is not a date: {moment!r}")
            continue
        moment = moment.replace(tzinfo=timezone.utc)
        try:
            amount = _amount(cells["Amount"])
        except ValueError as exc:
            errors.append(f"{where}: Amount {exc}")
            continue
        total += amount
        if amount == 0 or (sign and (amount > 0) != (sign > 0)):
            errors.append(f"{where}: {kind} with amount {amount}")
            continue
        source_id = _text(cells["ID"])
        if not source_id.isdigit() or source_id in seen_ids:
            errors.append(f"{where}: missing or repeated ID")
            continue
        seen_ids.add(source_id)
        comment = _text(cells["Comment"])
        ticker = _text(cells["Ticker"])
        day = moment.astimezone(local_tz).date()
        tx = dict(date=day, timestamp_utc=moment, account_id=account.account_id, institution=INSTITUTION,
                  type=our_type, amount=amount, currency=account.currency, description=comment,
                  source_id=source_id)

        if our_type in ("buy", "dividend") or kind == "Withholding tax":
            if not ticker:
                errors.append(f"{where}: {kind} without Ticker")
                continue
            instrument = reference.resolve(INSTITUTION, ticker)
            if instrument is None:
                missing.add(ticker)
                instrument = ""
            tx.update(instrument=instrument, isin=reference.isin_of(instrument) if instrument else "")

        if our_type == "buy":
            m = _BUY_RE.match(comment)
            if not m:
                errors.append(f"{where}: purchase comment not understood: {comment!r}")
                continue
            if m["ticker"] and m["ticker"] != ticker:
                errors.append(f"{where}: comment ticker {m['ticker']} != Ticker {ticker}")
                continue
            qty, price = Decimal(m["qty"]), Decimal(m["price"])
            if abs(qty * price + amount) > CENT:
                errors.append(f"{where}: amount {amount} != quantity x price ({qty} x {price}): "
                              "fee or currency conversion inside a purchase is not supported yet")
                continue
            tx.update(quantity=qty, price=price, price_currency=account.currency, fee=Decimal(0))
        elif our_type in ("dividend",) or kind == "Withholding tax":
            tx.update(group_id=f"{INSTITUTION}|{account.account_id}|div:{ticker}:{day.isoformat()}")
        elif kind in ("Free funds interest", "Free funds interest tax"):
            m = (_INTEREST_RE if kind == "Free funds interest" else _INTEREST_TAX_RE).match(comment)
            if not m:
                errors.append(f"{where}: interest comment not understood: {comment!r}")
                continue
            tx.update(group_id=f"{INSTITUTION}|{account.account_id}|int:{m['period']}")
        elif kind == "Transfer":
            m = _FX_RE.match(comment)
            if not m or account.currency not in (m["src"], m["dst"]):
                errors.append(f"{where}: transfer is not a currency conversion of this sub-account")
                continue
            if (m["src"] == account.currency) != (amount < 0):
                errors.append(f"{where}: conversion {m['src']}->{m['dst']} with amount {amount}")
                continue
        out.append(Transaction(**tx))

    if total_row is None:
        errors.append("Cash Operations: no Total row")
    else:
        try:
            stated = Decimal(repr(float(total_row[5])))
        except (TypeError, ValueError, IndexError):
            stated = None
        if stated is None or abs(stated - total) > Decimal("0.005"):
            errors.append(f"Cash Operations: sum of amounts {total} != Total {total_row[5] if len(total_row) > 5 else None}")
    if errors or missing:
        raise ParseError(errors[:MAX_ERRORS], sorted(missing))
    return out, notes
