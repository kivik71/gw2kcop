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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from gw2kcop.accounts import Account
from gw2kcop.config import Config
from gw2kcop.io.text import read_text
from gw2kcop.transactions import master, valuations, xtb
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
    target: str = "transactions.csv"
    note: str = ""
    member: str = ""  # file inside a ZIP (path is then the ZIP)


PREAMBLE_MAX_ROWS = 60  # a header further down than this is not looked for


def _first_rows(text: str, delimiter: str) -> list[list[str]]:
    rows = []
    for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        if any(cell.strip() for cell in row):
            rows.append(row)
            if len(rows) >= PREAMBLE_MAX_ROWS:
                break
    return rows


def _detect(text: str, specs: list[FormatSpec], institution: str) -> FormatSpec | None:
    for delimiter in sorted({s.delimiter for s in specs if s.institution == institution}):
        for n, row in enumerate(_first_rows(text, delimiter)):
            spec = detect(row, specs, institution, after_preamble=n > 0)
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


# Institutions parsed in code (not TOML): file suffix and format name shown to the user.
CODE_PARSERS = {xtb.INSTITUTION: (".xlsx", "xtb.statement")}


def import_code_file(path: Path, account: Account, reference: Reference, config: Config,
                     dry_run: bool) -> FileResult:
    """A file of an institution parsed in code (today: XTB statements)."""
    result = FileResult(path=path, account_id=account.account_id, status="error",
                        format=CODE_PARSERS[account.institution][1])
    result = _import_statement(path, path.name, account, reference, config, dry_run, result)
    if result.status == "imported":
        result.moved_to = _move_done(path)
    return result


ZIP_MAX_MEMBERS = 50
ZIP_MAX_MEMBER_BYTES = 20_000_000  # an XTB statement is well under 1 MB


def _zip_members(path: Path) -> tuple[list[tuple[str, bytes]], list[str]]:
    """``(xlsx members as (name, bytes), problems)``; reads in memory, never extracts."""
    problems: list[str] = []
    out: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > ZIP_MAX_MEMBERS:
                return [], [f"{len(infos)} files in the ZIP (limit {ZIP_MAX_MEMBERS})"]
            for info in infos:
                if not info.filename.lower().endswith(".xlsx"):
                    problems.append(f"{info.filename}: not .xlsx, not imported")
                    continue
                with zf.open(info) as handle:
                    data = handle.read(ZIP_MAX_MEMBER_BYTES + 1)  # do not trust the declared size
                if len(data) > ZIP_MAX_MEMBER_BYTES:
                    problems.append(f"{info.filename}: larger than {ZIP_MAX_MEMBER_BYTES} bytes, not imported")
                    continue
                out.append((info.filename, data))
    except (zipfile.BadZipFile, OSError) as exc:
        return [], [f"cannot read the ZIP: {exc}"]
    return out, problems


def import_zip(path: Path, accounts: list[Account], reference: Reference, config: Config,
               dry_run: bool, only_account: str | None = None) -> tuple[list[FileResult], list[str]]:
    """An XTB ZIP (one folder per account number, one statement each) in the month folder.

    Each statement goes to the account whose ``export_identifier`` equals the account
    number inside the file. The ZIP moves to ``_zaimportowane/`` only when every
    statement in it was imported.
    """
    by_number = {a.export_identifier: a for a in accounts
                 if a.institution == xtb.INSTITUTION and a.export_identifier and a.import_method == "parser"}
    members, notes = _zip_members(path)
    notes = [f"{path.name}: {n}" for n in notes]
    results: list[FileResult] = []
    complete = bool(members) and not notes
    for name, data in members:
        result = FileResult(path=path, account_id="?", status="error", format="xtb.statement", member=name)
        try:
            number = xtb.account_number(io.BytesIO(data))
        except ParseError as exc:
            result.message = "\n".join(exc.errors)
            results.append(result)
            complete = False
            continue
        account = by_number.get(number)
        folder = name.rsplit("/", 1)[0] if "/" in name else ""
        if account is None:
            result.message = "no xtb account in accounts.csv has this account number as export_identifier"
        elif folder and folder != number:
            result.message = "the folder name in the ZIP is not the account number inside the file"
        if result.message:
            results.append(result)
            complete = False
            continue
        if only_account and account.account_id != only_account:
            complete = False
            continue
        result = _import_statement(io.BytesIO(data), f"{path.name}/{name}", account, reference, config,
                                   dry_run, FileResult(path=path, account_id=account.account_id, status="error",
                                                       format="xtb.statement", member=name))
        results.append(result)
        complete = complete and result.status == "imported"
    if complete and not dry_run:
        moved = _move_done(path)
        for r in results:
            r.moved_to = moved
    elif not dry_run and members:
        notes.append(f"{path.name}: kept in place (not every file in it was imported)")
    return results, notes


def _import_statement(source, source_file: str, account: Account, reference: Reference, config: Config,
                      dry_run: bool, result: FileResult) -> FileResult:
    try:
        txs, notes = xtb.parse_statement(source, account, reference, config.timezone)
        result.note = "; ".join(notes)
        if dry_run:
            result.added, result.skipped = master.count_new(config.data_dir, txs)
            result.status = "checked"
            return result
        merged = master.merge(config.data_dir, txs, source_file)
    except ParseError as exc:
        result.message = "\n".join(exc.errors[:30])
        result.missing_aliases = exc.missing_aliases
        return result
    except master.TransactionsError as exc:
        result.message = str(exc)
        return result
    result.added, result.skipped, result.status = merged.added, merged.skipped, "imported"
    return result


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
    if spec.kind == "valuations":
        return _import_valuations(path, text, spec, account, config, dry_run, result)
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


def _import_valuations(path: Path, text: str, spec: FormatSpec, account: Account, config: Config,
                       dry_run: bool, result: FileResult) -> FileResult:
    try:
        parsed = valuations.parse_valuations(text, spec, account)
        if dry_run:
            result.added, result.skipped = valuations.count_new(config.data_dir, parsed.rows)
            result.status = "checked"
        else:
            result.added, result.skipped = valuations.merge(config.data_dir, parsed.rows, path.name)
            result.status = "imported"
    except ParseError as exc:
        result.message = "\n".join(exc.errors[:30])
        return result
    except master.TransactionsError as exc:
        result.message = str(exc)
        return result
    result.target = valuations.FILE_NAME
    if parsed.intraday_skipped:
        result.note = f"{parsed.intraday_skipped} row(s) with today's time not stored (day not closed)"
    if result.status == "imported":
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
            if entry.suffix.lower() == ".zip":
                zip_results, zip_notes = import_zip(entry, accounts, reference, config, dry_run, only_account)
                results.extend(zip_results)
                notes.extend(zip_notes)
                continue
            notes.append(f"{entry.name}: not in an account folder, not imported")
            continue
        account = by_id.get(entry.name)
        if account is None:
            zips = sorted(p for p in entry.iterdir() if p.is_file() and p.suffix.lower() == ".zip")
            if not zips:
                notes.append(f"{entry.name}/: no such account_id in accounts.csv, not imported")
                continue
            # A folder of multi-account ZIPs (e.g. TOM-XTB/): accounts come from the files.
            for path in zips:
                zip_results, zip_notes = import_zip(path, accounts, reference, config, dry_run, only_account)
                results.extend(zip_results)
                notes.extend(f"{entry.name}/{n}" for n in zip_notes)
            for path in sorted(entry.iterdir()):
                if path.is_file() and path.suffix.lower() != ".zip" and not path.name.startswith("."):
                    notes.append(f"{entry.name}/{path.name}: not a ZIP and not in an account folder, not imported")
            continue
        if only_account and account.account_id != only_account:
            continue
        files = sorted(p for p in entry.iterdir() if p.is_file() and not p.name.startswith("."))
        if account.import_method != "parser":
            continue  # manual accounts: pastes and PDFs are handled outside the importer
        if account.institution in CODE_PARSERS:
            suffix = CODE_PARSERS[account.institution][0]
            for path in files:
                if path.suffix.lower() != suffix:
                    notes.append(f"{entry.name}/{path.name}: only {suffix} is supported for this account, not imported")
                    continue
                results.append(import_code_file(path, account, reference, config, dry_run))
            continue
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
