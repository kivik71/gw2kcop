#!/usr/bin/env python3
"""Data guard: block commits that look like they contain real personal or financial data.

Runs as a pre-commit hook (on staged files) and in CI (on all tracked files).
Usage: python tools/data_guard.py FILE [FILE ...]

Checks (Guidelines 6.4):
- Polish account numbers: IBAN ``PL`` + 26 digits, or a bare 26-digit NRB (spaced or not)
- PESEL-like numbers: 11 digits with a valid PESEL checksum
- Card-like numbers: 13-19 digits passing the Luhn check
- E-mail addresses
- Files larger than 1 MB
- Every file under tests/fixtures/ must be marked SYNTHETIC: the word ``SYNTHETIC`` in its
  first 3 lines, or a sidecar file ``<name>.SYNTHETIC`` next to it (for binary files)

A line can be excused with the marker ``data-guard: allow`` (use only for synthetic values
that must look real, and say why in the same line).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_BYTES = 1_000_000
ALLOW_MARKER = "data-guard: allow"
FIXTURES_DIR = Path("tests/fixtures")
SKIP_SUFFIXES = {".SYNTHETIC"}
SKIP_NAMES = {"requirements.lock", "requirements-dev.lock"}  # only package hashes

IBAN_RE = re.compile(r"\bPL\s?\d{2}(?:\s?\d{4}){6}\b", re.IGNORECASE)
NRB_RE = re.compile(r"(?<![\dA-Za-z])\d{2}(?:[ ]?\d{4}){6}(?![\dA-Za-z])")
# Digit runs glued to letters (hashes, order ids like Z2613500000002) are not checked.
DIGITS_RE = re.compile(r"(?<![\dA-Za-z])\d{11,19}(?![\dA-Za-z])")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


def is_pesel(digits: str) -> bool:
    if len(digits) != 11:
        return False
    total = sum(int(d) * w for d, w in zip(digits[:10], PESEL_WEIGHTS))
    return (10 - total % 10) % 10 == int(digits[10])


def is_luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def scan_line(line: str) -> list[str]:
    if ALLOW_MARKER in line:
        return []
    findings = []
    if IBAN_RE.search(line):
        findings.append("IBAN-like account number")
    elif NRB_RE.search(line):
        findings.append("26-digit account number (NRB)")
    for match in DIGITS_RE.finditer(line):
        digits = match.group(0)
        if is_pesel(digits):
            findings.append("PESEL-like number")
        elif is_luhn(digits):
            findings.append("card-like number (Luhn)")
    if EMAIL_RE.search(line):
        findings.append("e-mail address")
    return findings


def is_fixture(path: Path) -> bool:
    try:
        path.relative_to(FIXTURES_DIR)
    except ValueError:
        return False
    return True


def has_synthetic_marker(path: Path, head: list[str]) -> bool:
    if path.with_name(path.name + ".SYNTHETIC").exists():
        return True
    return any("SYNTHETIC" in line for line in head[:3])


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
        return problems
    size = path.stat().st_size
    if size > MAX_BYTES:
        problems.append(f"{path}: file larger than 1 MB ({size} bytes)")
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace") if b"\x00" not in raw[:8000] else ""
    lines = text.splitlines()
    if is_fixture(path) and not has_synthetic_marker(path, lines):
        problems.append(f"{path}: fixture not marked SYNTHETIC (first 3 lines or sidecar file)")
    for number, line in enumerate(lines, start=1):
        for finding in scan_line(line):
            problems.append(f"{path}:{number}: {finding}")
    return problems


def main(argv: list[str]) -> int:
    problems: list[str] = []
    for name in argv:
        problems.extend(check_file(Path(name)))
    if problems:
        print("Data guard blocked this change (possible real data):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "If the value is synthetic, add 'data-guard: allow' to the line and explain why.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
