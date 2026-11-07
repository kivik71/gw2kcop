"""``gw2kcop prepare``: create the export folders for a month.

Layout (architecture, section 3):
    <exports>/RRRR-MM/<account_id>/   one per active account (parser and manual)
    <exports>/RRRR-MM/_nieprzypisane/ files that belong to no account yet
    <exports>/_start/<account_id>/    full histories at go-live (``--start``)

Idempotent: existing folders and files are never changed. New folders get mode 700
on macOS/Linux (owner only), as exports contain personal data.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from gw2kcop.accounts import Account

MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")
START_FOLDER = "_start"
UNASSIGNED_FOLDER = "_nieprzypisane"


@dataclass
class PrepareResult:
    root: Path
    created: list[Path] = field(default_factory=list)
    existing: list[Path] = field(default_factory=list)


def validate_month(value: str) -> str:
    if not MONTH_RE.match(value):
        raise ValueError(f"month must look like 2026-11, got {value!r}")
    return value


def _ensure_dir(path: Path, result: PrepareResult) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to use a symbolic link: {path}")
    if path.is_dir():
        result.existing.append(path)
        return
    if path.exists():
        raise ValueError(f"not a folder: {path}")
    path.mkdir(mode=0o700, parents=False)
    if os.name == "posix":
        path.chmod(0o700)
    result.created.append(path)


def prepare(exports_dir: Path, accounts: list[Account], month: str | None, start: bool = False) -> PrepareResult:
    """Create ``<exports>/<month or _start>/<account_id>/`` for each active account."""
    if start == (month is not None):
        raise ValueError("give either a month (RRRR-MM) or --start, not both")
    if not exports_dir.is_dir():
        raise ValueError(f"exports folder does not exist: {exports_dir}")
    name = START_FOLDER if start else validate_month(month or "")
    root = exports_dir / name
    result = PrepareResult(root=root)
    _ensure_dir(root, result)
    for account in accounts:
        if account.active:
            _ensure_dir(root / account.account_id, result)
    if not start:
        _ensure_dir(root / UNASSIGNED_FOLDER, result)
    return result
