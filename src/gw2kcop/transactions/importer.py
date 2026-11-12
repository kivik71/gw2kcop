"""``gw2kcop transactions import``: walk a month folder and import every export.

Layout (architecture, "Konwencja folderów eksportów"):
``<exports>/RRRR-MM/<account_id>/<original file>``; ``_start/`` holds full histories.
The account of a file is its folder name. A file's type is found from its header.
After a successful import the file is moved to ``<account_id>/_zaimportowane/``;
a file that fails stays where it is and nothing from it is written.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from gw2kcop.accounts import Account
from gw2kcop.config import Config
from gw2kcop.io.text import read_text
from gw2kcop.transactions import master
from gw2kcop.transactions.engine import ParseError, parse_export
from gw2kcop.transactions.formats import FormatSpec, detect
from gw2kcop.transactions.reference import Reference

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DONE_DIR = "_zaimportowane"


@dataclass
class FileResult:
    path: Path
    account_id: str
    status: str  # imported, checked, error, skipped
    format: str = ""
    added: int = 0
    skipped: int = 0
    message: str = ""
    missing_aliases: list[str] = field(default_factory=list)
    moved_to: Path | None = None


def _first_row(text: str, delimiter: str) -> list[str]:
    for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        if any(cell.strip() for cell in row):
            return row
    return []


def _detect(text: str, specs: list[FormatSpec], institution: str) -> FormatSpec | None:
    for delimiter in sorted({s.delimiter for s in specs if s.institution == institution}):
        spec = detect(_first_row(text, delimiter), specs, institution)
        if spec:
            return spec
    return None


def _move_done(path: Path) -> Path:
    target_dir = path.parent / DONE_DIR
    target_dir.mkdir(mode=0o700, exist_ok=True)
    target = target_dir / path.name
    n = 1
    while target.exists():  # never overwrite an earlier file with the same name
        target = target_dir / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.rename(target)
    return target


def import_file(path: Path, account: Account, specs: list[FormatSpec], reference: Reference,
                config: Config, dry_run: bool) -> FileResult:
    result = FileResult(path=path, account_id=account.account_id, status="error")
    try:
        text = read_text(path).text
    except ValueError as exc:
        result.message = str(exc)
        return result
    spec = _detect(text, specs, account.institution)
    if spec is None:
        result.message = f"unknown file type for {account.institution} (header not recognised)"
        return result
    result.format = spec.name
    try:
        txs = parse_export(text, spec, account, reference, config.timezone)
        if dry_run:
            result.added, result.skipped = master.count_new(config.data_dir, txs)
            result.status = "checked"
            return result
        merged = master.merge(config.data_dir, txs, path.name)
    except ParseError as exc:
        result.message = "\n".join(exc.errors[:30])
        result.missing_aliases = exc.missing_aliases
        return result
    except master.TransactionsError as exc:
        result.message = str(exc)
        return result
    result.added, result.skipped, result.status = merged.added, merged.skipped, "imported"
    result.moved_to = _move_done(path)
    return result


def import_folder(config: Config, accounts: list[Account], specs: list[FormatSpec],
                  reference: Reference, month: str | None, dry_run: bool = False,
                  only_account: str | None = None) -> tuple[list[FileResult], list[str]]:
    """Import every export under one month folder (or ``_start``).

    Returns per-file results and notes about things that were not imported.
    """
    if month is not None and not MONTH_RE.match(month):
        raise ValueError(f"month must be RRRR-MM, got {month!r}")
    root = config.exports_dir / (month or "_start")
    if not root.is_dir():
        raise ValueError(f"folder not found: {root} (run 'gw2kcop prepare' first)")
    by_id = {a.account_id: a for a in accounts}
    results: list[FileResult] = []
    notes: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_file():
            notes.append(f"{entry.name}: not in an account folder, not imported")
            continue
        account = by_id.get(entry.name)
        if account is None:
            notes.append(f"{entry.name}/: no such account_id in accounts.csv, not imported")
            continue
        if only_account and account.account_id != only_account:
            continue
        files = sorted(p for p in entry.iterdir() if p.is_file() and not p.name.startswith("."))
        if account.import_method != "parser":
            continue  # manual accounts: pastes and PDFs are handled outside the importer
        if not any(s.institution == account.institution for s in specs):
            if files:
                notes.append(f"{entry.name}/: no parser yet for {account.institution}, {len(files)} file(s) left")
            continue
        for path in files:
            if path.suffix.lower() != ".csv":
                notes.append(f"{entry.name}/{path.name}: only .csv is supported for this account, not imported")
                continue
            results.append(import_file(path, account, specs, reference, config, dry_run))
    return results, notes
