"""The master ``transactions.csv``: schema, keys, deduplication and safe writes.

Schema approved 2026-11-10 (see the project's transactions schema document).
Rules implemented here:

- one row per event; a trade fee is already included in ``amount`` and shown in ``fee``;
- ``key`` is ``institution|account_id|source_id`` when the export gives an id, else
  ``institution|account_id|h:<16 hex of SHA-256>|<n>`` where ``n`` counts identical
  rows within one file;
- a row whose ``key`` is already in the master file is skipped; existing rows are
  never edited or deleted by an import;
- text cells starting with ``= + - @`` (or tab / CR) get a leading ``'`` so a
  spreadsheet does not run them as formulas;
- the file is written atomically, after a copy of the previous version is kept in
  ``.backup/``; every merge appends one line to ``import_log.csv``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from gw2kcop.io.text import read_text

COLUMNS = (
    "key",
    "date",
    "timestamp_utc",
    "account_id",
    "institution",
    "type",
    "instrument",
    "isin",
    "quantity",
    "price",
    "price_currency",
    "amount",
    "currency",
    "fee",
    "description",
    "source_id",
    "group_id",
    "source_file",
    "imported_at",
)
TYPES = frozenset({
    "deposit", "withdrawal", "internal_transfer", "buy", "sell", "dividend", "interest",
    "fee", "tax", "charge", "fx_exchange", "expense", "income", "ppk_employee",
    "ppk_employer", "ppk_state", "fund_switch", "other",
})
NUMERIC_COLUMNS = frozenset({"quantity", "price", "amount", "fee"})
LOG_COLUMNS = ("imported_at", "source_file", "account_id", "rows_in", "added", "skipped", "status", "message")
BACKUPS_KEPT = 20

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_ACCOUNT_ID_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_CURRENCY_RE = re.compile(r"^(?:[A-Z]{3}|GBp)$")  # GBp: LSE quotes in pence
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class TransactionsError(ValueError):
    """A transaction or the master file breaks the schema."""


@dataclass(frozen=True)
class Transaction:
    """One row of ``transactions.csv`` as produced by a parser (before the key is set)."""

    date: date
    account_id: str
    institution: str
    type: str
    amount: Decimal
    currency: str
    description: str = ""
    timestamp_utc: datetime | None = None
    instrument: str = ""
    isin: str = ""
    quantity: Decimal | None = None
    price: Decimal | None = None
    price_currency: str = ""
    fee: Decimal | None = None
    source_id: str = ""
    group_id: str = ""


@dataclass
class MergeResult:
    added: int = 0
    skipped: int = 0
    keys_added: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- formatting


def format_decimal(value: Decimal | None) -> str:
    """Plain dot-decimal text, no exponent and no thousands separator."""
    if value is None:
        return ""
    if not value.is_finite():
        raise TransactionsError(f"number is not finite: {value}")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def neutralize(text: str) -> str:
    """Prefix ``'`` so a spreadsheet shows the text instead of running a formula."""
    return "'" + text if text.startswith(_FORMULA_START) else text


def format_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise TransactionsError("timestamp_utc must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime(_TS_FORMAT)


# ---------------------------------------------------------------- validation


def validate(tx: Transaction) -> None:
    """Raise ``TransactionsError`` if ``tx`` breaks a schema rule."""
    where = f"{tx.account_id} {tx.date} {tx.type}"
    if tx.type not in TYPES:
        raise TransactionsError(f"{where}: unknown type {tx.type!r}")
    if not _ACCOUNT_ID_RE.match(tx.account_id):
        raise TransactionsError(f"{where}: invalid account_id")
    if not _SLUG_RE.match(tx.institution):
        raise TransactionsError(f"{where}: invalid institution {tx.institution!r}")
    if not _CURRENCY_RE.match(tx.currency):
        raise TransactionsError(f"{where}: invalid currency {tx.currency!r}")
    if tx.price_currency and not _CURRENCY_RE.match(tx.price_currency):
        raise TransactionsError(f"{where}: invalid price_currency {tx.price_currency!r}")
    if tx.isin and not _ISIN_RE.match(tx.isin):
        raise TransactionsError(f"{where}: invalid ISIN {tx.isin!r}")
    if tx.fee is not None and tx.fee < 0:
        raise TransactionsError(f"{where}: fee must be >= 0")
    if tx.price is not None and tx.price < 0:
        raise TransactionsError(f"{where}: price must be >= 0")
    if tx.type in ("buy", "sell"):
        if not tx.instrument:
            raise TransactionsError(f"{where}: {tx.type} needs an instrument")
        if tx.quantity is None or tx.quantity == 0:
            raise TransactionsError(f"{where}: {tx.type} needs a quantity")
        if (tx.type == "buy") != (tx.quantity > 0):
            raise TransactionsError(f"{where}: quantity sign does not match {tx.type}")
    for name in ("amount", "quantity", "price", "fee"):
        value = getattr(tx, name)
        if value is not None and not value.is_finite():
            raise TransactionsError(f"{where}: {name} is not a finite number")
    if "|" in tx.source_id:
        raise TransactionsError(f"{where}: source_id must not contain '|'")


# ---------------------------------------------------------------- keys


def _hash_part(tx: Transaction) -> str:
    fields = (
        tx.date.isoformat(), tx.type, format_decimal(tx.amount), tx.currency,
        tx.instrument, format_decimal(tx.quantity), tx.description,
    )
    digest = hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()
    return digest[:16]


def assign_keys(transactions: Iterable[Transaction]) -> list[tuple[str, Transaction]]:
    """Give each transaction of ONE source file its key.

    Rows without ``source_id`` get a content hash plus the occurrence number of an
    identical row within the file, so two genuinely identical operations stay two
    rows, and importing the same file again produces the same keys.
    """
    seen: Counter[str] = Counter()
    out: list[tuple[str, Transaction]] = []
    for tx in transactions:
        prefix = f"{tx.institution}|{tx.account_id}|"
        if tx.source_id:
            key = prefix + tx.source_id
            seen[key] += 1
            if seen[key] > 1:
                # Two rows with one id in one file: the parser must merge or
                # disambiguate them, otherwise the second would be lost.
                raise TransactionsError(f"duplicate source_id in one file: {tx.source_id!r}")
        else:
            base = prefix + "h:" + _hash_part(tx)
            seen[base] += 1
            key = f"{base}|{seen[base]}"
        out.append((key, tx))
    return out


# ---------------------------------------------------------------- rows


def to_row(key: str, tx: Transaction, source_file: str, imported_at: datetime) -> dict[str, str]:
    validate(tx)
    row = {
        "key": key,
        "date": tx.date.isoformat(),
        "timestamp_utc": format_utc(tx.timestamp_utc) if tx.timestamp_utc else "",
        "account_id": tx.account_id,
        "institution": tx.institution,
        "type": tx.type,
        "instrument": tx.instrument,
        "isin": tx.isin,
        "quantity": format_decimal(tx.quantity),
        "price": format_decimal(tx.price),
        "price_currency": tx.price_currency,
        "amount": format_decimal(tx.amount),
        "currency": tx.currency,
        "fee": format_decimal(tx.fee),
        "description": tx.description,
        "source_id": tx.source_id,
        "group_id": tx.group_id,
        "source_file": source_file,
        "imported_at": format_utc(imported_at),
    }
    return {k: v if k in NUMERIC_COLUMNS else neutralize(v) for k, v in row.items()}


def read_master(path: Path) -> list[dict[str, str]]:
    """Read the master file; a missing file is an empty master. Fails on a wrong header."""
    if not path.exists():
        return []
    text = read_text(path).text
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise TransactionsError(f"{path.name}: header does not match the approved schema")
    rows = list(reader)
    keys = Counter(row["key"] for row in rows)
    dupes = [k for k, n in keys.items() if n > 1]
    if dupes:
        raise TransactionsError(f"{path.name}: duplicate keys, e.g. {dupes[0]!r}")
    return rows


def _render(columns: tuple[str, ...], rows: Iterable[dict[str, str]]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _backup(path: Path, now: datetime) -> Path | None:
    if not path.exists():
        return None
    folder = path.parent / ".backup"
    folder.mkdir(mode=0o700, exist_ok=True)
    target = folder / f"{path.stem}-{now.strftime('%Y%m%dT%H%M%S%fZ')}{path.suffix}"
    shutil.copy2(path, target)
    old = sorted(folder.glob(f"{path.stem}-*{path.suffix}"))
    for stale in old[:-BACKUPS_KEPT]:
        stale.unlink()
    return target


def append_log(data_dir: Path, entry: dict[str, str]) -> None:
    log = data_dir / "import_log.csv"
    new = not log.exists()
    with open(log, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_COLUMNS, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow({k: neutralize(str(entry.get(k, ""))) for k in LOG_COLUMNS})
    if new:
        os.chmod(log, 0o600)


def merge(data_dir: Path, transactions: Iterable[Transaction], source_file: str,
          now: datetime | None = None) -> MergeResult:
    """Add the new rows of one source file to ``<data_dir>/transactions.csv``.

    All rows are validated first; one invalid row writes nothing (fail closed).
    """
    now = now or datetime.now(timezone.utc)
    path = data_dir / "transactions.csv"
    txs = list(transactions)
    keyed = assign_keys(txs)
    new_rows = [to_row(key, tx, source_file, now) for key, tx in keyed]  # validates all
    accounts = sorted({tx.account_id for tx in txs})

    existing = read_master(path)
    known = {row["key"] for row in existing}
    result = MergeResult()
    to_add: list[dict[str, str]] = []
    for row in new_rows:
        if row["key"] in known:
            result.skipped += 1
            continue
        known.add(row["key"])
        to_add.append(row)
        result.keys_added.append(row["key"])
    result.added = len(to_add)

    if to_add:
        _backup(path, now)
        _atomic_write(path, _render(COLUMNS, [*existing, *to_add]))
    append_log(data_dir, {
        "imported_at": format_utc(now),
        "source_file": source_file,
        "account_id": ",".join(accounts),
        "rows_in": str(len(txs)),
        "added": str(result.added),
        "skipped": str(result.skipped),
        "status": "ok",
        "message": "",
    })
    return result
