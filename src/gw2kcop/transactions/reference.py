"""Reference files: ``instruments.csv`` and ``instrument_aliases.csv``.

An export names an instrument its own way (``KGHM``, ``EXH1 GR ETF``, an ISIN in a
dividend text). ``instrument_aliases.csv`` maps ``(institution, alias)`` to our
canonical ``ticker``, which must exist in ``instruments.csv``. One ISIN can belong
to several listings, so the import never picks a ticker from an ISIN on its own.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from gw2kcop.io.text import read_text

ALIAS_COLUMNS = ("institution", "alias", "ticker")


class ReferenceError(ValueError):
    """instruments.csv or instrument_aliases.csv is missing or inconsistent."""


@dataclass
class Reference:
    tickers: dict[str, dict[str, str]] = field(default_factory=dict)  # ticker -> row
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)  # (institution, alias) -> ticker

    def resolve(self, institution: str, alias: str) -> str | None:
        return self.aliases.get((institution, alias.strip()))

    def isin_of(self, ticker: str) -> str:
        return self.tickers.get(ticker, {}).get("isin", "")


def _rows(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ReferenceError(f"file not found: {path}")
    reader = csv.DictReader(io.StringIO(read_text(path).text, newline=""))
    header = [h.strip() for h in reader.fieldnames or ()]
    missing = [c for c in required if c not in header]
    if missing:
        raise ReferenceError(f"{path.name}: missing columns {', '.join(missing)}")
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def load_reference(data_dir: Path) -> Reference:
    ref = Reference()
    for row in _rows(data_dir / "instruments.csv", ("ticker", "isin")):
        if not row["ticker"]:
            continue
        if row["ticker"] in ref.tickers:
            raise ReferenceError(f"instruments.csv: duplicate ticker {row['ticker']!r}")
        ref.tickers[row["ticker"]] = row
    alias_file = data_dir / "instrument_aliases.csv"
    if not alias_file.exists():
        return ref  # first run: every alias is reported as missing
    errors = []
    for n, row in enumerate(_rows(alias_file, ALIAS_COLUMNS), start=2):
        if not any(row.values()):
            continue
        key = (row["institution"], row["alias"])
        if row["ticker"] not in ref.tickers:
            errors.append(f"instrument_aliases.csv:{n}: ticker {row['ticker']!r} is not in instruments.csv")
        elif key in ref.aliases and ref.aliases[key] != row["ticker"]:
            errors.append(f"instrument_aliases.csv:{n}: alias {row['alias']!r} maps to two tickers")
        ref.aliases[key] = row["ticker"]
    if errors:
        raise ReferenceError("\n".join(errors))
    return ref
