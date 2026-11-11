"""Generic CSV engine: turn one export file into transactions, driven by a FormatSpec.

Fail closed: any unexpected value (unknown side or operation text, a number that
does not parse, a failed integrity check, an unknown instrument alias) stops the
whole file. Errors are collected so the user sees all problems of a file at once.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

from gw2kcop.accounts import Account
from gw2kcop.io.dates import DATE_FORMATS, parse_date, parse_datetime
from gw2kcop.io.numbers import parse_decimal, parse_optional_decimal
from gw2kcop.transactions.formats import FormatSpec
from gw2kcop.transactions.master import Transaction
from gw2kcop.transactions.reference import Reference

MAX_ERRORS = 30
CENT = Decimal("0.01")


class ParseError(ValueError):
    """The file cannot be imported; ``errors`` and ``missing_aliases`` say why."""

    def __init__(self, errors: list[str], missing_aliases: list[str]):
        self.errors = errors
        self.missing_aliases = missing_aliases
        parts = errors[:MAX_ERRORS]
        if missing_aliases:
            parts.append(f"unknown instrument aliases: {', '.join(missing_aliases)}")
        super().__init__("\n".join(parts))


@dataclass
class _Ctx:
    spec: FormatSpec
    account: Account
    reference: Reference
    local_tz: ZoneInfo
    errors: list[str] = field(default_factory=list)
    missing: set[str] = field(default_factory=set)

    def fail(self, line: int, message: str) -> None:
        self.errors.append(f"line {line}: {message}")


def read_rows(text: str, spec: FormatSpec) -> list[tuple[int, dict[str, str], str]]:
    """Data rows as ``(line number, {column: value}, raw line)``; header checked by caller."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=spec.delimiter)
    out = []
    header_seen = False
    for row in reader:
        n = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        if not header_seen:
            header_seen = True
            continue
        cells = [c.strip() for c in row]
        while len(cells) > len(spec.header) and not cells[-1]:
            cells.pop()
        if len(cells) != len(spec.header):
            raise ParseError([f"line {n}: expected {len(spec.header)} columns, got {len(cells)}"], [])
        out.append((n, dict(zip(spec.header, cells)), spec.delimiter.join(cells)))
    return out


def _moment(ctx: _Ctx, text: str, line: int):
    """Return ``(local date, aware timestamp or None)``."""
    spec = ctx.spec
    try:
        if spec.datetime_format in DATE_FORMATS:
            return parse_date(text, spec.datetime_format), None
        moment = parse_datetime(text, spec.datetime_format, spec.timezone)
    except ValueError:
        ctx.fail(line, f"date {text!r} does not match {spec.datetime_format}")
        return None, None
    if spec.date_only:
        if (moment.hour, moment.minute, moment.second) != (0, 0, 0):
            ctx.fail(line, f"expected a date without time, got {text!r}")
        return moment.date(), None
    return moment.astimezone(ctx.local_tz).date(), moment


def _num(ctx: _Ctx, row: dict[str, str], key: str, line: int, optional: bool = False) -> Decimal | None:
    column = ctx.spec.columns.get(key)
    if column is None:
        return None
    text = row[column]
    try:
        value = parse_optional_decimal(text, ctx.spec.decimal) if optional else parse_decimal(text, ctx.spec.decimal)
    except ValueError:
        ctx.fail(line, f"{column}: not a number: {text!r}")
        return None
    return value


def _currency(ctx: _Ctx, row: dict[str, str], line: int) -> str:
    if ctx.spec.currency == "account":
        return ctx.account.currency
    value = row[ctx.spec.columns["currency"]]
    if len(value) != 3 or not value.isalpha() or not value.isupper():
        ctx.fail(line, f"invalid currency {value!r}")
    return value


def _instrument(ctx: _Ctx, alias: str) -> str:
    ticker = ctx.reference.resolve(ctx.spec.institution, alias)
    if ticker is None:
        ctx.missing.add(alias)
        return ""
    return ticker


def _trades(ctx: _Ctx, rows) -> list[Transaction]:
    spec, cols, out = ctx.spec, ctx.spec.columns, []
    fee_currencies = spec.checks.get("fee_currencies")
    for line, row, raw in rows:
        before = len(ctx.errors)
        side = spec.sides.get(row[cols["side"]])
        if side is None:
            ctx.fail(line, f"unknown side {row[cols['side']]!r}")
            continue
        qty = _num(ctx, row, "quantity", line)
        price = _num(ctx, row, "price", line)
        gross = _num(ctx, row, "gross", line)
        fee = _num(ctx, row, "fee", line, optional=True) if "fee" in cols else None
        fx = _num(ctx, row, "fx_rate", line, optional=True) if "fx_rate" in cols else None
        day, moment = _moment(ctx, row[cols["time"]], line)
        currency = _currency(ctx, row, line)
        if len(ctx.errors) > before:
            continue
        if qty <= 0 or price < 0 or gross < 0 or (fee is not None and fee < 0):
            ctx.fail(line, "quantity must be > 0 and price, value and fee >= 0 in this format")
            continue
        if spec.checks.get("gross_equals_qty_price_fx"):
            expected = qty * price * (fx if fx is not None else Decimal(1))
            if abs(expected - gross) > CENT:
                ctx.fail(line, f"value {gross} != quantity x price x rate ({expected.quantize(CENT)})")
                continue
        if fee and fee_currencies is not None and currency not in fee_currencies:
            ctx.fail(line, f"fee currency for settlement in {currency} is not confirmed for this format")
            continue
        fee = fee or Decimal(0)
        alias = row[cols["instrument"]]
        ticker = _instrument(ctx, alias)
        signed_qty = qty if side == "buy" else -qty
        amount = -(gross + fee) if side == "buy" else gross - fee
        out.append(Transaction(
            date=day, timestamp_utc=moment, account_id=ctx.account.account_id,
            institution=spec.institution, type=side, instrument=ticker,
            isin=ctx.reference.isin_of(ticker), quantity=signed_qty, price=price,
            price_currency=row[cols["price_currency"]], amount=amount, currency=currency,
            fee=fee, description=raw.strip(),
        ))
    return out


def _cash(ctx: _Ctx, rows) -> list[Transaction]:
    spec, cols, out = ctx.spec, ctx.spec.columns, []
    for line, row, _raw in rows:
        before = len(ctx.errors)
        direction = spec.directions.get(row[cols["direction"]])
        if direction is None:
            ctx.fail(line, f"unknown operation direction {row[cols['direction']]!r}")
            continue
        amount = _num(ctx, row, "amount", line)
        day, moment = _moment(ctx, row[cols["date"]], line)
        currency = _currency(ctx, row, line)
        if len(ctx.errors) > before:
            continue
        if (direction == "credit") != (amount > 0):
            ctx.fail(line, f"{row[cols['direction']]} with amount {amount}")
            continue
        if spec.checks.get("balance_chain"):
            b0 = _num(ctx, row, "balance_before", line)
            b1 = _num(ctx, row, "balance_after", line)
            if b0 is None or b1 is None:
                continue
            if b0 + amount != b1:
                ctx.fail(line, f"balance check failed: {b0} + {amount} != {b1}")
                continue
        text = row[cols["description"]]
        rule = next((r for r in spec.rules if r.direction in ("any", direction) and r.pattern.search(text)), None)
        if rule is None:
            ctx.fail(line, f"unknown operation text: {text[:80]!r}")
            continue
        found = rule.pattern.search(text).groupdict()
        isin = found.get("isin") or ""
        ticker = _instrument(ctx, isin) if isin else ""
        group = ""
        if rule.group:
            try:
                group = f"{spec.institution}|{ctx.account.account_id}|" + rule.group.format(**found)
            except (KeyError, IndexError):
                ctx.fail(line, f"rule group {rule.group!r} needs a field the pattern does not capture")
                continue
        out.append(Transaction(
            date=day, timestamp_utc=moment, account_id=ctx.account.account_id,
            institution=spec.institution, type=rule.type, instrument=ticker, isin=isin,
            amount=amount, currency=currency, description=text, group_id=group,
        ))
    return out


def parse_export(text: str, spec: FormatSpec, account: Account, reference: Reference,
                 local_tz: ZoneInfo) -> list[Transaction]:
    """Parse one export file. Raises ``ParseError`` listing every problem found."""
    ctx = _Ctx(spec, account, reference, local_tz)
    rows = read_rows(text, spec)
    txs = _trades(ctx, rows) if spec.kind == "trades" else _cash(ctx, rows)
    if ctx.errors or ctx.missing:
        raise ParseError(ctx.errors, sorted(ctx.missing))
    return txs
