"""mBank "Historia wycen" -> broker_valuations.csv (synthetic data only)."""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions.engine import ParseError
from gw2kcop.transactions.formats import FormatError, builtin_specs, parse_spec
from gw2kcop.transactions.master import TransactionsError
from gw2kcop.transactions.valuations import merge, parse_valuations

ACCOUNT = Account("TOM-TEST", "mbank_brokerage", "tom", "", "Test", "brokerage", "PLN", True, "parser")
HEADER = "Data;Wycena całkowita PLN"
EXPORT = "\n".join([
    HEADER,
    "30.12.2025 00:00:00;0",
    "31.12.2025 00:00:00;1234,50000000",
    "01.01.2026 00:00:00;1234,50000000",
    "02.01.2026 00:00:00;1300,12345678",
    "02.01.2026 16:52:05;1310,00",
    "",
])


def spec():
    return next(s for s in builtin_specs() if s.name == "mbank_brokerage.valuations")


def test_parse_skips_intraday_row():
    parsed = parse_valuations(EXPORT, spec(), ACCOUNT)
    assert [v.date for v in parsed.rows] == [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]
    assert parsed.rows[-1].value_pln == Decimal("1300.12345678") and parsed.rows[0].value_pln == 0
    assert parsed.intraday_skipped == 1


@pytest.mark.parametrize("line,message", [
    ("31.12.2025 00:00:00;-1,00", "negative"),
    ("31.12.2025 00:00:00;abc", "not a number"),
    ("31.12.2025;1,00", "does not match"),
    ("30.12.2025 00:00:00;5,00", "repeats line"),
])
def test_parse_fails_closed(line, message):
    bad = "\n".join([HEADER, "30.12.2025 00:00:00;0", line, ""])
    with pytest.raises(ParseError, match=message):
        parse_valuations(bad, spec(), ACCOUNT)


def test_merge_same_value_known_changed_value_stops(tmp_path):
    rows = parse_valuations(EXPORT, spec(), ACCOUNT).rows
    assert merge(tmp_path, rows, "a.csv") == (4, 0)
    assert merge(tmp_path, rows, "b.csv") == (0, 4)
    text = (tmp_path / "broker_valuations.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "date,account_id,value_pln,source_file"
    assert "2026-01-02,TOM-TEST,1300.12345678,a.csv" in text and "2025-12-31,TOM-TEST,1234.5,a.csv" in text
    changed = parse_valuations(EXPORT.replace("1300,12345678", "1300,13"), spec(), ACCOUNT).rows
    with pytest.raises(TransactionsError, match="changed values"):
        merge(tmp_path, changed, "c.csv")
    assert (tmp_path / "broker_valuations.csv").read_text(encoding="utf-8") == text


def test_valuations_spec_needs_pln():
    data = {"kind": "valuations", "decimal": ",", "datetime_format": "DD.MM.YYYY",
            "header": ["Data", "Wartość"], "columns": {"date": "Data", "value": "Wartość"}}
    with pytest.raises(FormatError, match="PLN"):
        parse_spec(data, "x.toml")
    assert parse_spec({**data, "currency": "PLN"}, "x.toml").kind == "valuations"


def test_import_command_writes_valuations_and_moves_file(tmp_path, capsys):
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "TOM-TEST,mbank_brokerage,tom,,Test,brokerage,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text("ticker,name,isin\n", encoding="utf-8")
    month = exports / "2026-01" / "TOM-TEST"
    month.mkdir(parents=True)
    (month / "Historia wycen 1 2026.csv").write_text("﻿" + EXPORT, encoding="utf-8")
    assert main(["--config", str(cfg), "transactions", "import", "2026-01", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would add 4, already known 0 (mbank_brokerage.valuations -> broker_valuations.csv)" in out
    assert "1 row(s) with today's time not stored" in out
    assert main(["--config", str(cfg), "transactions", "import", "2026-01"]) == 0
    assert (month / "_zaimportowane" / "Historia wycen 1 2026.csv").exists()
    rows = list(csv.DictReader(io.StringIO((data / "broker_valuations.csv").read_text(encoding="utf-8"))))
    assert len(rows) == 4 and not (data / "transactions.csv").exists()
