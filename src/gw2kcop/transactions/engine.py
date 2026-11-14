"""Generic CSV engine: turn one export file into transactions, driven by a FormatSpec.

Fail closed: any unexpected value (unknown side or operation text, a number that
does not parse, a failed integrity check, an unknown instrument alias) stops the
whole file. Errors are collected so the user sees all problems of a file at once.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from gw2kcop.accounts import Account
from gw2kcop.io.dates import DATE_FORMATS, parse_date, parse_datetime
from gw2kcop.io.numbers import parse_decimal, parse_optional_decimal
from gw2kcop.transactions.formats import FormatSpec, clean_header
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
    """Data rows as ``(line number, {column key: value}, raw line)``.

    The header must be the first non-empty row, or, if the format has a preamble,
    any row; rows before it are not read. Values are keyed by ``spec.keys``.
    """
    text = text.removeprefix("\ufeff")  # BOM left in by a caller that did not use read_text
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=spec.delimiter)
    width = len(spec.header)
    overflow = spec.keys.index(spec.overflow_column) if spec.overflow_column else None
    out = []
    header_seen = False
    for row in reader:
        n = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        if not header_seen:
            if clean_header(row) == spec.header:
                header_seen = True
            elif not spec.preamble:
                raise ParseError([f"line {n}: header does not match {spec.name}"], [])
            continue
        cells = [c.strip() for c in row]
        while len(cells) > width and not cells[-1]:
            cells.pop()
        if len(cells) > width and overflow is not None:
            # The text column contains the delimiter unquoted: glue the extra cells back.
            extra = len(cells) - width
            joined = spec.delimiter.join(cells[overflow:overflow + extra + 1]).strip()
            cells = cells[:overflow] + [joined] + cells[overflow + extra + 1:]
        if len(cells) != width:
            raise ParseError([f"line {n}: expected {width} columns, got {len(cells)}"], [])
        out.append((n, dict(zip(spec.keys, cells)), spec.delimiter.join(cells)))
    if not header_seen:
        raise ParseError([f"header of {spec.name} not found"], [])
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
    fee_same_only = bool(spec.checks.get("fee_only_same_currency"))
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
        price_currency = row[cols["price_currency"]]
        # Without a rate column a cross-currency value cannot be recomputed; it is taken as exported.
        if spec.checks.get("gross_equals_qty_price_fx") and ("fx_rate" in cols or price_currency == currency):
            expected = qty * price * (fx if fx is not None else Decimal(1))
            if abs(expected - gross) > CENT:
                ctx.fail(line, f"value {gross} != quantity x price x rate ({expected.quantize(CENT)})")
                continue
        if "fee_currency" in cols and row[cols["fee_currency"]] != currency:
            ctx.fail(line, f"fee in {row[cols['fee_currency']]} on a trade settled in {currency}")
            continue
        if fee and fee_same_only and price_currency != currency:
            ctx.fail(line, f"fee on a {price_currency} trade settled in {currency}: fee currency not confirmed")
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
            price_currency=price_currency, amount=amount, currency=currency,
            fee=fee, description=raw.strip(),
        ))
    return out


def _cash(ctx: _Ctx, rows) -> list[Transaction]:
    spec, cols, out = ctx.spec, ctx.spec.columns, []
    for line, row, _raw in rows:
        before = len(ctx.errors)
        amount = _num(ctx, row, "amount", line)
        day, moment = _moment(ctx, row[cols["date"]], line)
        currency = _currency(ctx, row, line)
        if len(ctx.errors) > before:
            continue
        if "direction" in cols:
            direction = spec.directions.get(row[cols["direction"]])
            if direction is None:
                ctx.fail(line, f"unknown operation direction {row[cols['direction']]!r}")
                continue
            if (direction == "credit") != (amount > 0):
                ctx.fail(line, f"{row[cols['direction']]} with amount {amount}")
                continue
        elif amount == 0:
            ctx.fail(line, "amount 0: direction unknown")
            continue
        else:
            direction = "credit" if amount > 0 else "debit"
        if spec.checks.get("balance_chain"):
            b0 = _num(ctx, row, "balance_before", line)
            b1 = _num(ctx, row, "balance_after", line)
            if b0 is None or b1 is None:
                continue
            if b0 + amount != b1:
                ctx.fail(line, f"balance check failed: {b0} + {amount} != {b1}")
                continue
        if "account" in cols and ctx.account.export_identifier \
                and row[cols["account"]] != ctx.account.export_identifier:
            ctx.fail(line, "the account in the file is not this account's export_identifier")
            continue
        text = row[cols["description"]]
        rule = next((r for r in spec.rules if r.direction in ("any", direction) and r.pattern.search(text)), None)
        if rule is None:
            ctx.fail(line, f"unknown operation text: {text[:80]!r}")
            continue
        if rule.type == "ignore":
            continue
        found = rule.pattern.search(text).groupdict()
        isin = found.get("isin") or ""
        alias = found.get("alias") or ""
        if isin:
            ticker = _instrument(ctx, isin)
        elif alias:
            ticker = _instrument(ctx, alias)
            isin = ctx.reference.isin_of(ticker)
        else:
            ticker = ""
        trade = _trade_fields(ctx, rule.type, found, amount, currency, line) if rule.type in ("buy", "sell") else {}
        if trade is None:
            continue
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
            amount=amount, currency=currency, description=text, group_id=group, **trade,
            source_id=row[cols["id"]] if "id" in cols else "",
        ))
    return out


def _trade_fields(ctx: _Ctx, side: str, found: dict, amount: Decimal, currency: str, line: int) -> dict | None:
    """Quantity, price and fee of a trade settlement row; the fee is what the
    settlement holds beyond quantity x price (rounded to cents)."""
    try:
        qty, price = Decimal(found["quantity"]), Decimal(found["price"])
    except InvalidOperation:
        ctx.fail(line, f"quantity or price is not a number: {found['quantity']!r}, {found['price']!r}")
        return None
    price_currency = found.get("price_currency") or currency
    if qty <= 0 or price < 0:
        ctx.fail(line, "quantity must be > 0 and price >= 0")
        return None
    if price_currency != currency:
        ctx.fail(line, f"price in {price_currency}, settlement in {currency}: not supported without a sample")
        return None
    gross = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
    fee = -amount - gross if side == "buy" else gross - amount
    if fee < 0:
        ctx.fail(line, f"settlement {amount} is better than quantity x price ({gross}): fee would be negative")
        return None
    return {"quantity": qty if side == "buy" else -qty, "price": price,
            "price_currency": price_currency, "fee": fee}


def _summary(ctx: _Ctx, rows):
    """Drop the summary row (last row, ``total_row`` column empty) after checking its sums."""
    spec = ctx.spec
    if not spec.total_row:
        return rows
    if not rows or rows[-1][1][spec.total_row]:
        raise ParseError([f"the summary row (empty {spec.total_row}) is missing at the end"], [])
    line, total, _raw = rows[-1]
    rows = rows[:-1]
    for line_n, row, _ in rows:
        if not row[spec.total_row]:
            ctx.fail(line_n, f"{spec.total_row} is empty before the summary row")
    for key in spec.checks.get("total_sums", []):
        column = spec.columns[key]
        values = [_num(ctx, row, key, n, optional=True) for n, row, _ in rows]
        expected = _num(ctx, total, key, line, optional=True)
        if ctx.errors:
            break
        found = sum((v for v in values if v is not None), Decimal(0))
        if expected is None or found != expected:
            ctx.fail(line, f"summary row: {column} {expected} != sum of rows {found}")
    return rows


def parse_export(text: str, spec: FormatSpec, account: Account, reference: Reference,
                 local_tz: ZoneInfo) -> list[Transaction]:
    """Parse one export file. Raises ``ParseError`` listing every problem found."""
    ctx = _Ctx(spec, account, reference, local_tz)
    rows = read_rows(text, spec)
    rows = _summary(ctx, rows)
    for column, value in spec.checks.get("equals", {}).items():
        for line, row, _raw in rows:
            if row[column] != value:
                ctx.fail(line, f"{column} is {row[column]!r}, only {value!r} is imported")
    if ctx.errors:
        raise ParseError(ctx.errors, [])
    txs = _trades(ctx, rows) if spec.kind == "trades" else _cash(ctx, rows)
    if ctx.errors or ctx.missing:
        raise ParseError(ctx.errors, sorted(ctx.missing))
    return txs
