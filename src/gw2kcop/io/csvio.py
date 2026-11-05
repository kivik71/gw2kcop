"""CSV reading for exports: delimiter detection and row access."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from gw2kcop.io.text import read_text

DEFAULT_DELIMITERS = (";", ",", "\t")


class DelimiterError(ValueError):
    """No candidate delimiter splits the sample consistently."""


def sniff_delimiter(sample: str, candidates: tuple[str, ...] = DEFAULT_DELIMITERS) -> str:
    """Pick the delimiter that gives the most columns consistently across sample lines.

    Quoted fields are respected. Lines with fewer columns (metadata rows above the
    header, summary rows) are tolerated; the winner is the candidate whose most
    common column count (> 1) occurs in the most lines.
    """
    lines = [line for line in sample.splitlines() if line.strip()][:50]
    best: tuple[int, int, str] | None = None
    for delimiter in candidates:
        counts: dict[int, int] = {}
        for row in csv.reader(lines, delimiter=delimiter):
            if len(row) > 1:
                counts[len(row)] = counts.get(len(row), 0) + 1
        if not counts:
            continue
        width, hits = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        score = (hits, width, delimiter)
        if best is None or score[:2] > best[:2]:
            best = score
    if best is None:
        raise DelimiterError("could not detect the CSV delimiter")
    return best[2]


def read_csv_rows(
    path: str | Path,
    delimiter: str | None = None,
) -> tuple[list[list[str]], str, str]:
    """Return ``(rows, delimiter, encoding)`` for a CSV export.

    Rows are lists of raw strings (no type conversion). Empty lines are dropped.
    """
    decoded = read_text(path)
    used = delimiter or sniff_delimiter(decoded.text)
    reader = csv.reader(io.StringIO(decoded.text, newline=""), delimiter=used)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    return rows, used, decoded.encoding
