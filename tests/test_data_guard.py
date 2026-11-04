"""Tests for tools/data_guard.py.

Sensitive-looking values are assembled at runtime so that this file itself
passes the data guard.
"""

import importlib.util
from pathlib import Path

import pytest

GUARD_PATH = Path(__file__).resolve().parents[1] / "tools" / "data_guard.py"
spec = importlib.util.spec_from_file_location("data_guard", GUARD_PATH)
data_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data_guard)


def _pesel() -> str:
    # Widely published PESEL example with a valid checksum.
    return "".join(["4405", "1401", "359"])


def _card() -> str:
    # Standard Visa test number (Luhn-valid).
    return "".join(["4111", "1111", "1111", "1111"])


@pytest.mark.parametrize(
    "line, expected",
    [
        ("PL" + "61" + " 1090" * 6, "IBAN-like account number"),
        ("61" + "1090" * 6, "26-digit account number (NRB)"),
        ("id;" + _pesel() + ";x", "PESEL-like number"),
        ("card " + _card(), "card-like number (Luhn)"),
        ("contact: jan" + "@" + "example.com", "e-mail address"),
    ],
)
def test_detects_sensitive_values(line, expected):
    assert expected in data_guard.scan_line(line)


def test_allow_marker_skips_line():
    assert data_guard.scan_line(_card() + "  # data-guard: allow (test value)") == []


@pytest.mark.parametrize(
    "line",
    [
        "2026-05-15;Sprzedaz;4;0,625;2,50",
        "order 26423031 operation 37911082",
        "Z2613500000002",
    ],
)
def test_ordinary_lines_pass(line):
    assert data_guard.scan_line(line) == []


def test_large_file_is_blocked(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (data_guard.MAX_BYTES + 1))
    assert any("larger than 1 MB" in p for p in data_guard.check_file(big))


def test_fixture_requires_synthetic_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fixtures = Path("tests/fixtures")
    fixtures.mkdir(parents=True)
    unmarked = fixtures / "sample.csv"
    unmarked.write_text("a;b\n1;2\n", encoding="utf-8")
    assert any("not marked SYNTHETIC" in p for p in data_guard.check_file(unmarked))

    marked = fixtures / "marked.csv"
    marked.write_text("# SYNTHETIC\na;b\n", encoding="utf-8")
    assert data_guard.check_file(marked) == []

    binary = fixtures / "sample.xlsx"
    binary.write_bytes(b"PK\x03\x04\x00\x00")
    (fixtures / "sample.xlsx.SYNTHETIC").write_text("", encoding="utf-8")
    assert data_guard.check_file(binary) == []


def test_main_exit_codes(tmp_path):
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see\n", encoding="utf-8")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("card " + _card() + "\n", encoding="utf-8")
    assert data_guard.main([str(clean)]) == 0
    assert data_guard.main([str(dirty)]) == 1
