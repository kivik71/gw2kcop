"""Read and validate ``accounts.csv``.

Columns (all required, in any order):
account_id, institution, owner, export_identifier, name, account_type, currency,
active, import_method
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from gw2kcop.io.text import read_text

COLUMNS = (
    "account_id",
    "institution",
    "owner",
    "export_identifier",
    "name",
    "account_type",
    "currency",
    "active",
    "import_method",
)
ACCOUNT_TYPES = {
    "current",
    "savings",
    "deposit",
    "brokerage",
    "ike",
    "ikze",
    "ppk",
    "fx",
    "esop",
    "unit_linked",
    "bonds",
}
IMPORT_METHODS = {"parser", "manual"}
# Upper-case letters/digits in at least two dash-separated parts, e.g. OWNA-XTB-PLN.
# Also guarantees the id is a safe folder name (no dots, slashes or spaces).
ACCOUNT_ID_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SLUG_RE = re.compile(r"^[a-z0-9_]+$")


class AccountsError(ValueError):
    """accounts.csv is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Account:
    account_id: str
    institution: str
    owner: str
    export_identifier: str
    name: str
    account_type: str
    currency: str
    active: bool
    import_method: str


def _bool(value: str, where: str) -> bool:
    upper = value.strip().upper()
    if upper == "TRUE":
        return True
    if upper == "FALSE":
        return False
    raise AccountsError(f"{where}: active must be TRUE or FALSE, got {value!r}")


def parse_accounts(text: str, source: str = "accounts.csv") -> list[Account]:
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [name.strip() for name in (reader.fieldnames or [])]
    missing = [col for col in COLUMNS if col not in header]
    if missing:
        raise AccountsError(f"{source}: missing columns: {', '.join(missing)}")
    accounts: list[Account] = []
    seen: set[str] = set()
    errors: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        where = f"{source}:{line_no}"
        try:
            account = Account(
                account_id=row["account_id"],
                institution=row["institution"],
                owner=row["owner"],
                export_identifier=row["export_identifier"],
                name=row["name"],
                account_type=row["account_type"],
                currency=row["currency"],
                active=_bool(row["active"], where),
                import_method=row["import_method"],
            )
        except AccountsError as exc:
            errors.append(str(exc))
            continue
        if not ACCOUNT_ID_RE.match(account.account_id):
            errors.append(f"{where}: invalid account_id {account.account_id!r} (e.g. OWNA-XTB-PLN)")
        elif account.account_id in seen:
            errors.append(f"{where}: duplicate account_id {account.account_id!r}")
        seen.add(account.account_id)
        if not SLUG_RE.match(account.institution):
            errors.append(f"{where}: institution must be lower-case letters, digits or _")
        if not SLUG_RE.match(account.owner):
            errors.append(f"{where}: owner must be lower-case letters, digits or _")
        if account.account_type not in ACCOUNT_TYPES:
            errors.append(f"{where}: unknown account_type {account.account_type!r}")
        if not CURRENCY_RE.match(account.currency):
            errors.append(f"{where}: currency must be a 3-letter ISO code, got {account.currency!r}")
        if account.import_method not in IMPORT_METHODS:
            errors.append(f"{where}: import_method must be parser or manual (no guessing)")
        accounts.append(account)
    if errors:
        raise AccountsError("\n".join(errors))
    return accounts


def load_accounts(path: Path) -> list[Account]:
    if not path.is_file():
        raise AccountsError(f"accounts file not found: {path}")
    return parse_accounts(read_text(path).text, source=path.name)
