"""Manual opening balances: ``<data>/opening_balances.csv`` -> ``transactions.csv``.

For positions bought before the history an export can show (no confirmations left),
the owner writes one row per position, or one cash row per account:

    account_id,date,instrument,quantity,price,price_currency,amount,note
    OWNA-TEST,2016-12-31,WSE:TEST,10,25.40,PLN,,average cost from the broker's holdings report
    OWNA-TEST,2016-12-31,,,,,1200.00,cash at the start

- A position row has ``instrument`` (a ticker from instruments.csv), ``quantity`` > 0 and
  ``price`` (average cost per unit); ``amount`` stays empty and is written as 0, because
  the money left the account before the known history.
- A cash row has no instrument, quantity or price; ``amount`` is the cash in the
  account's currency.
- Numbers use a dot as the decimal separator; the date is YYYY-MM-DD.

Rows become type ``opening_balance`` with key ``<institution>|<account_id>|open:<ticker or
cash>:<date>``, so running the command again adds nothing. A changed value for an
existing key is not updated: the import reports it as already known.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from gw2kcop.accounts import Account
from gw2kcop.io.text import read_text
from gw2kcop.transactions.master import Transaction
from gw2kcop.transactions.reference import Reference

FILE_NAME = "opening_balances.csv"
COLUMNS = ("account_id", "date", "instrument", "quantity", "price", "price_currency", "amount", "note")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class OpeningError(ValueError):
    """opening_balances.csv has problems; ``errors`` lists all of them."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _number(text: str, where: str, name: str, errors: list[str]) -> Decimal | None:
    if not _NUMBER_RE.match(text):
        errors.append(f"{where}: {name} {text!r} is not a number like 12.50 (dot, no spaces)")
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - the pattern already guarantees a number
        errors.append(f"{where}: {name} {text!r} is not a number")
        return None


def read_opening(path: Path, accounts: list[Account], reference: Reference) -> list[Transaction]:
    """Read and check every row; raise ``OpeningError`` listing all problems."""
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    header = tuple(h.strip() for h in reader.fieldnames or ())
    if header != COLUMNS:
        raise OpeningError([f"{path.name}: header must be {','.join(COLUMNS)}"])
    by_id = {a.account_id: a for a in accounts}
    errors: list[str] = []
    out: list[Transaction] = []
    for row in reader:
        where = f"{path.name}:{reader.line_num}"
        if None in row:
            errors.append(f"{where}: too many values; a value with a comma must be in double quotes")
            continue
        r = {k.strip(): (v or "").strip() for k, v in row.items()}
        if not any(r.values()):
            continue
        before = len(errors)
        account = by_id.get(r["account_id"])
        if account is None:
            errors.append(f"{where}: account_id {r['account_id']!r} is not in accounts.csv")
            continue
        try:
            day = date.fromisoformat(r["date"])
        except ValueError:
            errors.append(f"{where}: date {r['date']!r} must be YYYY-MM-DD")
            continue
        if r["instrument"]:
            if r["instrument"] not in reference.tickers:
                errors.append(f"{where}: instrument {r['instrument']!r} is not a ticker in instruments.csv")
            if r["amount"]:
                errors.append(f"{where}: a position row has no amount (the cost is quantity x price)")
            qty = _number(r["quantity"], where, "quantity", errors)
            price = _number(r["price"], where, "price", errors)
            if not _CURRENCY_RE.match(r["price_currency"]):
                errors.append(f"{where}: price_currency {r['price_currency']!r} must be like PLN")
            if qty is not None and qty <= 0:
                errors.append(f"{where}: quantity must be > 0")
            if price is not None and price < 0:
                errors.append(f"{where}: price must be >= 0")
            if len(errors) > before:
                continue
            out.append(Transaction(
                date=day, account_id=account.account_id, institution=account.institution,
                type="opening_balance", instrument=r["instrument"],
                isin=reference.isin_of(r["instrument"]), quantity=qty, price=price,
                price_currency=r["price_currency"], amount=Decimal(0), currency=account.currency,
                description=r["note"] or "opening balance",
                source_id=f"open:{r['instrument']}:{day.isoformat()}",
            ))
        else:
            if r["quantity"] or r["price"] or r["price_currency"]:
                errors.append(f"{where}: a cash row has only amount (no quantity, price or price_currency)")
            amount = _number(r["amount"], where, "amount", errors)
            if amount is not None and amount == 0:
                errors.append(f"{where}: amount 0: leave the row out")
            if len(errors) > before:
                continue
            out.append(Transaction(
                date=day, account_id=account.account_id, institution=account.institution,
                type="opening_balance", amount=amount, currency=account.currency,
                description=r["note"] or "opening balance",
                source_id=f"open:cash:{day.isoformat()}",
            ))
    if errors:
        raise OpeningError(errors)
    return out
