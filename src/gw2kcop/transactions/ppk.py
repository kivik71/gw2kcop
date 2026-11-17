"""PPK (employee capital plans) transaction histories: Nationale-Nederlanden (.xls) and ING (.csv).

Format notes: research/formats/ppk-nn-2026-11.md and ppk-ing-2026-11.md (samples 2026-11-03).
Written in code, not TOML: NN is a legacy .xls with a metadata block, ING has a metadata
block with the participant number, and every row becomes two transactions.

How a row is stored (decision 2026-11-17): a PPK account holds fund units, never cash.
- A payment (employee, employer, state) is two rows with one ``group_id``:
  ``ppk_employee`` / ``ppk_employer`` / ``ppk_state`` with ``+amount`` (money paid in) and
  ``buy`` with ``-amount``, the units and the unit price. The cash of the account stays 0.
- A fund switch (NN) is two ``fund_switch`` rows with one ``group_id``: the old fund with
  negative units and ``+amount``, the new fund with positive units and ``-amount``.
- Both rows of a pair carry the valuation date (the day the units were bought or sold).

Fail closed: an unknown transaction type, status, header, account number or currency,
units x price far from the amount, a tax or a switch whose two sides do not net to zero
stops the whole file.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from gw2kcop.accounts import Account
from gw2kcop.io.text import read_text
from gw2kcop.transactions.engine import MAX_ERRORS, ParseError
from gw2kcop.transactions.master import Transaction
from gw2kcop.transactions.reference import Reference

CURRENCY = "PLN"
CENT = Decimal("0.01")
PRICE_PLACES = Decimal("0.000001")

# ---------------------------------------------------------------- NN (.xls)

NN_INSTITUTION = "nn"
NN_HEADER = ("Data zlecenia", "Data wyceny", "Typ transakcji", "Okres składki", "Kwota (PLN)",
             "Wartość rachunku po transakcji (PLN)", "Nazwa funduszu", "Cena jednostki (PLN)",
             "Liczba jednostek", "Wartość jednostek (PLN)", "Podatek (PLN)")
NN_HEADER_ROW = 5
NN_ACCOUNT_LABEL = "Nr rachunku PPK"
NN_TYPES = {
    "Wpłata podstawowa pracownika": "ppk_employee",
    "Wpłata podstawowa pracodawcy": "ppk_employer",
    "Dopłata roczna od państwa": "ppk_state",
    "Wpłata powitalna od państwa": "ppk_state",
    "Zmiana funduszu (umorzenie)": "switch_out",
    "Zmiana funduszu (nabycie)": "switch_in",
}

# ---------------------------------------------------------------- ING (.csv)

ING_INSTITUTION = "ing"
ING_HEADER = ("Date", "Transaction status", "Transaction type", "Detailed type of transaction",
              "Product type", "Product", "Fund name", "Amount", "Number of units", "P.U. category")
ING_PARTICIPANT_RE = re.compile(r"^(?P<number>\d+) - PPK\b")
ING_TYPES = {
    "PPK Primary employee's payment": "ppk_employee",
    "PPK Primary employer's payment": "ppk_employer",
    "PPK Annual payment": "ppk_state",
    "PPK Welcome payment": "ppk_state",
}
# A rebalance row has no amount, the new fund and its units (seen once, 2025-10-05: all units of the
# previous target-date fund moved to the next one; the file has no row for the fund that was sold).
# Accepted only when, from the purchases before it in the file:
# - exactly one other fund was held and is never bought again afterwards: two fund_switch rows
#   (old fund: minus all its units; new fund: plus the rebalance units), amount 0, no price,
#   because the export gives no value; or
# - only the same fund was held, with exactly these units: nothing changed, no row is written.
# Anything else stops the file.
ING_REBALANCE = "PPK Rebalance"

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def _float_decimal(value, places: int) -> Decimal:
    """A numeric .xls cell (a float) as an exact decimal with at most ``places`` decimals."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"not a number: {value!r}")
    exact = Decimal(f"{value:.{places}f}")
    if abs(Decimal(repr(value)) - exact) > Decimal("0.0000001"):
        raise ValueError(f"more than {places} decimals: {value!r}")
    return exact


def _text_decimal(text: str) -> Decimal:
    """``"1 039,94"`` -> 1039.94 (space or no-break space as thousands separator)."""
    cleaned = text.replace("\xa0", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not a number: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a number: {text!r}")
    return value


def _check_units(where: str, units: Decimal, price: Decimal, amount: Decimal, errors: list[str]) -> bool:
    """Units x price must give the amount, within the rounding of the unit price (2 decimals at NN)."""
    tolerance = CENT + abs(units) * Decimal("0.005")
    if abs(units * price - amount) > tolerance:
        errors.append(f"{where}: units x price ({units} x {price}) is far from the amount {amount}")
        return False
    return True


def _pair(account: Account, institution: str, day: date, kind: str, amount: Decimal, units: Decimal,
          price: Decimal, instrument: str, isin: str, description: str) -> list[Transaction]:
    """A payment and the purchase of units it paid for."""
    group = f"{institution}|{account.account_id}|ppk:{day.isoformat()}:{kind}:{amount}:{units}"
    common = {"date": day, "account_id": account.account_id, "institution": institution, "currency": CURRENCY,
              "description": description, "group_id": group}
    return [
        Transaction(type=kind, amount=amount, **common),
        Transaction(type="buy", amount=-amount, instrument=instrument, isin=isin, quantity=units,
                    price=price, price_currency=CURRENCY, fee=Decimal(0), **common),
    ]


def _resolve(reference: Reference, institution: str, fund: str, missing: set[str]) -> tuple[str, str]:
    ticker = reference.resolve(institution, fund)
    if ticker is None:
        missing.add(fund)
        return "", ""
    isin = reference.isin_of(ticker)
    # A PPK fund may have only a national fund code in the isin column (e.g. "RFe 40"): not an ISIN.
    return ticker, isin if _ISIN_RE.match(isin) else ""


# ---------------------------------------------------------------- NN


def _nn_rows(path: Path) -> list[list]:
    import xlrd  # only PPK NN needs it
    from xlrd.compdoc import CompDocError

    try:
        book = xlrd.open_workbook(str(path))
    except (xlrd.XLRDError, CompDocError, OSError, ValueError, IndexError) as exc:  # not a readable .xls
        raise ParseError([f"cannot read as .xls: {exc}"], []) from None
    if book.nsheets != 1:
        raise ParseError([f"not an NN PPK history: {book.nsheets} sheets"], [])
    sheet = book.sheet_by_index(0)
    return [sheet.row_values(r) for r in range(sheet.nrows)]


def _cell_text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def parse_nn(path: Path, account: Account, reference: Reference) -> tuple[list[Transaction], list[str]]:
    """Parse an NN "Historia transakcji finansowych PPK" export; raises ``ParseError``."""
    return parse_nn_rows(_nn_rows(path), account, reference)


def parse_nn_rows(rows: list[list], account: Account, reference: Reference) -> tuple[list[Transaction], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    missing: set[str] = set()
    if len(rows) < NN_HEADER_ROW or tuple(_cell_text(c) for c in rows[NN_HEADER_ROW - 1]) != NN_HEADER:
        raise ParseError([f"row {NN_HEADER_ROW} is not the known NN PPK header"], [])
    number = ""
    for row in rows[:NN_HEADER_ROW - 1]:
        cells = [_cell_text(c) for c in row]
        if NN_ACCOUNT_LABEL in cells:
            idx = cells.index(NN_ACCOUNT_LABEL)
            number = cells[idx + 1] if idx + 1 < len(cells) else ""
    if not number:
        errors.append(f"no '{NN_ACCOUNT_LABEL}' in the first rows")
    elif account.export_identifier:
        if number != account.export_identifier:
            errors.append(f"PPK account number in the file does not match export_identifier of {account.account_id}")
    else:
        notes.append(f"export_identifier of {account.account_id} is empty: PPK account number not checked")
    if account.currency != CURRENCY:
        errors.append(f"account currency is {account.currency}, NN PPK is in {CURRENCY}")

    out: list[Transaction] = []
    switches: dict[date, list[tuple[str, Decimal, Decimal, Decimal, str, str, str]]] = defaultdict(list)
    for n, raw in enumerate(rows[NN_HEADER_ROW:], start=NN_HEADER_ROW + 1):
        if not any(_cell_text(c) for c in raw):
            continue
        cells = dict(zip(NN_HEADER, list(raw) + [""] * len(NN_HEADER)))
        where = f"row {n}"
        kind_text = _cell_text(cells["Typ transakcji"])
        kind = NN_TYPES.get(kind_text)
        if kind is None:
            errors.append(f"{where}: unknown transaction type {kind_text!r} (not seen in a sample yet)")
            continue
        order_day, value_day = _cell_text(cells["Data zlecenia"]), _cell_text(cells["Data wyceny"])
        if not (_DATE_RE.match(order_day) and _DATE_RE.match(value_day)):
            errors.append(f"{where}: dates are not RRRR-MM-DD: {order_day!r}, {value_day!r}")
            continue
        try:
            amount = _float_decimal(cells["Kwota (PLN)"], 2)
            units = _float_decimal(cells["Liczba jednostek"], 6)
            price = _float_decimal(cells["Cena jednostki (PLN)"], 6)
            units_value = _float_decimal(cells["Wartość jednostek (PLN)"], 2)
            tax = _float_decimal(cells["Podatek (PLN)"] or 0.0, 2)
        except (TypeError, ValueError) as exc:
            errors.append(f"{where}: {exc}")
            continue
        if tax != 0:
            errors.append(f"{where}: tax {tax} is not supported yet (send an anonymised sample)")
            continue
        if units_value != amount:
            errors.append(f"{where}: value of units {units_value} != amount {amount}")
            continue
        outgoing = kind == "switch_out"
        if amount == 0 or units == 0 or (amount < 0) != outgoing or (units < 0) != outgoing:
            errors.append(f"{where}: {kind_text} with amount {amount} and units {units}")
            continue
        if not _check_units(where, units, price, amount, errors):
            continue
        fund = _cell_text(cells["Nazwa funduszu"])
        if not fund:
            errors.append(f"{where}: no fund name")
            continue
        instrument, isin = _resolve(reference, NN_INSTITUTION, fund, missing)
        day = date.fromisoformat(value_day)
        period = _cell_text(cells["Okres składki"])
        description = kind_text + (f" {period}" if period else "") + f" (zlecenie {order_day})"
        if kind.startswith("switch"):
            switches[day].append((kind, amount, units, price, instrument, isin, description))
            continue
        out.extend(_pair(account, NN_INSTITUTION, day, kind, amount, units, price, instrument, isin, description))

    for day, legs in sorted(switches.items()):
        outs = [leg for leg in legs if leg[0] == "switch_out"]
        ins = [leg for leg in legs if leg[0] == "switch_in"]
        if not outs or not ins or sum(leg[1] for leg in legs) != 0:
            errors.append(f"fund switch on {day}: the sold and bought amounts do not net to zero")
            continue
        group = f"{NN_INSTITUTION}|{account.account_id}|switch:{day.isoformat()}"
        for _, amount, units, price, instrument, isin, description in outs + ins:
            out.append(Transaction(date=day, account_id=account.account_id, institution=NN_INSTITUTION,
                                   type="fund_switch", amount=-amount, currency=CURRENCY, instrument=instrument,
                                   isin=isin, quantity=units, price=price, price_currency=CURRENCY,
                                   description=description, group_id=group))

    if errors or missing:
        raise ParseError(errors[:MAX_ERRORS], sorted(missing))
    out.sort(key=lambda t: (t.date, t.group_id, t.type == "buy"))
    return out, notes


# ---------------------------------------------------------------- ING


def _dmy(text: str) -> date | None:
    m = _DMY_RE.match(text)
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def parse_ing(path: Path, account: Account, reference: Reference) -> tuple[list[Transaction], list[str]]:
    """Parse an ING PPK "Transaction history" CSV (English portal); raises ``ParseError``."""
    try:
        text = read_text(path).text
    except ValueError as exc:
        raise ParseError([str(exc)], []) from None
    return parse_ing_text(text, account, reference)


def parse_ing_text(text: str, account: Account, reference: Reference) -> tuple[list[Transaction], list[str]]:
    rows = list(csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline="")))
    errors: list[str] = []
    notes: list[str] = []
    missing: set[str] = set()
    if not rows or [c.strip() for c in rows[0]][:1] != ["Transaction history"]:
        raise ParseError(["not an ING PPK transaction history (first line is not 'Transaction history')"], [])
    header_at = next((i for i, r in enumerate(rows) if tuple(c.strip() for c in r) == ING_HEADER), None)
    if header_at is None:
        raise ParseError(["the known ING PPK header row was not found (portal in English?)"], [])
    numbers = set()
    for i, row in enumerate(rows[:header_at]):
        if [c.strip() for c in row] == ["Participant numbers"] and i + 1 < header_at:
            for cell in rows[i + 1]:
                m = ING_PARTICIPANT_RE.match(cell.strip())
                if m:
                    numbers.add(m["number"])
    if len(numbers) != 1:
        errors.append("cannot read one participant number from the 'Participant numbers' block")
    elif account.export_identifier:
        if account.export_identifier not in numbers:
            errors.append(f"participant number in the file does not match export_identifier of {account.account_id}")
    else:
        notes.append(f"export_identifier of {account.account_id} is empty: participant number not checked")
    if account.currency != CURRENCY:
        errors.append(f"account currency is {account.currency}, ING PPK is in {CURRENCY}")

    out: list[Transaction] = []
    bought: list[tuple[date, str, Decimal]] = []
    rebalances: list[tuple[str, date, str, Decimal]] = []
    for n, raw in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(c.strip() for c in raw):
            continue
        where = f"line {n}"
        if len(raw) != len(ING_HEADER):
            errors.append(f"{where}: expected {len(ING_HEADER)} columns, got {len(raw)}")
            continue
        cells = {k: v.strip() for k, v in zip(ING_HEADER, raw)}
        kind_text = cells["Detailed type of transaction"]
        if (kind_text, cells["Transaction type"]) == (ING_REBALANCE, "Rebalance"):
            day = _dmy(cells["Date"])
            try:
                units = _text_decimal(cells["Number of units"])
            except ValueError as exc:
                errors.append(f"{where}: {exc}")
                continue
            if day is None or cells["Amount"] or cells["Transaction status"] != "Complete" or units <= 0:
                errors.append(f"{where}: rebalance row not understood (date, empty amount, units, status)")
                continue
            rebalances.append((where, day, cells["Fund name"], units))
            continue
        kind = ING_TYPES.get(kind_text)
        if kind is None:
            errors.append(f"{where}: unknown transaction type {kind_text!r} (not seen in a sample yet)")
            continue
        if cells["Transaction status"] != "Complete" or cells["Transaction type"] != "Purchase":
            errors.append(f"{where}: {cells['Transaction status']!r} {cells['Transaction type']!r} "
                          "is not supported yet (only Complete Purchase)")
            continue
        if cells["Product type"] != "PPK":
            errors.append(f"{where}: product type {cells['Product type']!r} is not PPK")
            continue
        day = _dmy(cells["Date"])
        if day is None:
            errors.append(f"{where}: date is not DD.MM.RRRR: {cells['Date']!r}")
            continue
        try:
            amount = _text_decimal(cells["Amount"])
            units = _text_decimal(cells["Number of units"])
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            continue
        if amount <= 0 or units <= 0:
            errors.append(f"{where}: {kind_text} with amount {amount} and units {units}")
            continue
        fund = cells["Fund name"]
        if not fund:
            errors.append(f"{where}: no fund name")
            continue
        price = (amount / units).quantize(PRICE_PLACES)  # ING gives no unit price
        instrument, isin = _resolve(reference, ING_INSTITUTION, fund, missing)
        description = f"{kind_text} (P.U. {cells['P.U. category']})"
        out.extend(_pair(account, ING_INSTITUTION, day, kind, amount, units, price, instrument, isin, description))
        bought.append((day, fund, units))

    for where, day, fund, units in rebalances:
        held: dict[str, Decimal] = defaultdict(Decimal)
        for d, f, u in bought:
            if d < day:
                held[f] += u
        others = {f: u for f, u in held.items() if f != fund and u}
        later = {f for d, f, _ in bought if d > day and f in others}
        if not others and held.get(fund) == units:
            notes.append(f"rebalance on {day}: {units} units of {fund!r} = all units held, no change, not imported")
        elif len(others) == 1 and not held.get(fund) and not later:
            ((old_fund, old_units),) = others.items()
            group = f"{ING_INSTITUTION}|{account.account_id}|switch:{day.isoformat()}"
            for name, qty in ((old_fund, -old_units), (fund, units)):
                instrument, isin = _resolve(reference, ING_INSTITUTION, name, missing)
                out.append(Transaction(date=day, account_id=account.account_id, institution=ING_INSTITUTION,
                                       type="fund_switch", amount=Decimal(0), currency=CURRENCY,
                                       instrument=instrument, isin=isin, quantity=qty,
                                       description=f"{ING_REBALANCE}: {old_fund} -> {fund} (value not in export)",
                                       group_id=group))
            notes.append(f"rebalance on {day}: {old_units} units of {old_fund!r} -> {units} units of {fund!r}")
        else:
            found = ", ".join(f"{f}: {u}" for f, u in sorted(held.items())) or "none"
            errors.append(f"{where}: rebalance to {units} units of {fund!r} not understood; units held before "
                          f"{day} in this file: {found} (export the full history from the first payment)")

    if errors or missing:
        raise ParseError(errors[:MAX_ERRORS], sorted(missing))
    out.sort(key=lambda t: (t.date, t.group_id, t.type == "buy"))
    return out, notes
