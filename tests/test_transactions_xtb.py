"""XTB statements (.xlsx) built in the test; synthetic data only."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions.engine import ParseError
from gw2kcop.transactions.reference import Reference
from gw2kcop.transactions.xtb import CASH_HEADER, parse_statement

WARSAW = ZoneInfo("Europe/Warsaw")
NUMBER = "00000001"  # synthetic account number
ACCOUNT = Account("TOM-TEST-USD", "xtb", "tom", NUMBER, "Test USD", "brokerage", "USD", True, "parser")
CONV = f"Currency conversion, PLN to USD from TA: 00000002 to: {NUMBER}, Exchange rate:0.25"
CASH = [
    ("Deposit", "", "", "", datetime(2026, 8, 4, 8, 15), 100.0, "1001", "Test bank deposit", "My Trades", ""),
    ("Transfer", "", "", "", datetime(2026, 8, 4, 9, 0), 250.0, "1002", CONV, "My Trades", ""),
    ("Stock purchase", "Test Co", "TEST.US", "STOCK", datetime(2026, 8, 4, 19, 25), -200.0, "1003",
     "OPEN BUY TEST.US 2/2.5 @ 100.00", "My Trades", "9001"),
    ("Stock purchase", "Test Co", "TEST.US", "STOCK", datetime(2026, 8, 4, 19, 26), -50.0, "1004",
     "OPEN BUY 0.5/2.5 @ 100.00", "My Trades", "9001"),
    ("Dividend", "Test Co", "TEST.US", "STOCK", datetime(2026, 9, 30, 22, 30), 1.0, "1005",
     "TEST.US USD 0.40/ SHR", "My Trades", ""),
    ("Withholding tax", "Test Co", "TEST.US", "STOCK", datetime(2026, 9, 30, 22, 30), -0.15, "1006",
     "TEST.US USD WHT 15%", "My Trades", ""),
    ("Free funds interest", "", "", "", datetime(2026, 10, 3, 14, 0), 0.06, "1007",
     "Free-funds Interest 2026-09", "My Trades", ""),
    ("Free funds interest tax", "", "", "", datetime(2026, 10, 3, 14, 0), -0.02, "1008",
     "Free-funds Interest Tax 2026-09", "My Trades", ""),
]


def build(path, cash=CASH, number=NUMBER, currency="USD", closed=(), total=None):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    meta = [("Account number", number), ("x", ""), ("Date from (UTC)", datetime(2025, 12, 31, 23)),
            ("Date to (UTC)", datetime(2026, 11, 3))]
    ws = wb.create_sheet("Closed Positions")
    for row in [*meta, ("Instrument", "Ticker"), *closed, ("Profit/loss",)]:
        ws.append(row)
    ws = wb.create_sheet("Cash Operations")
    for row in [*meta, CASH_HEADER, *cash]:
        ws.append(row)
    ws.append(("Total", None, None, None, None,
               sum(r[5] for r in cash) if total is None else total))
    ws = wb.create_sheet("Open Positions")
    for row in [("Account number", number), ("Open Positions", ""), ("Data as of", datetime(2026, 11, 3)),
                ("Product", "Metric", "Amount", "Currency"), ("My Trades", "Open position value", 1.0, currency)]:
        ws.append(row)
    wb.save(path)
    return path


def ref():
    r = Reference()
    r.tickers["NYSE:TEST"] = {"ticker": "NYSE:TEST", "isin": "US0000000001"}
    r.aliases[("xtb", "TEST.US")] = "NYSE:TEST"
    return r


def test_cash_operations(tmp_path):
    txs, notes = parse_statement(build(tmp_path / "a.xlsx"), ACCOUNT, ref(), WARSAW)
    assert notes == []
    assert [t.type for t in txs] == ["deposit", "fx_exchange", "buy", "buy", "dividend", "tax", "interest", "tax"]
    dep, fx, buy, part, div, wht, intr, itax = txs
    assert dep.source_id == "1001" and dep.amount == Decimal("100.00") and dep.currency == "USD"
    assert dep.timestamp_utc.isoformat() == "2026-08-04T08:15:00+00:00"
    assert (buy.quantity, buy.price, buy.amount, buy.fee) == (Decimal(2), Decimal("100.00"), Decimal("-200.00"), 0)
    assert buy.instrument == "NYSE:TEST" and buy.isin == "US0000000001" and buy.price_currency == "USD"
    assert part.quantity == Decimal("0.5")
    assert div.date == date(2026, 10, 1)  # 22:30 UTC = 00:30 Warsaw next day
    assert div.group_id == wht.group_id == "xtb|TOM-TEST-USD|div:TEST.US:2026-10-01"
    assert intr.group_id == itax.group_id == "xtb|TOM-TEST-USD|int:2026-09"
    assert fx.amount == Decimal("250.00")


@pytest.mark.parametrize("change,message", [
    (lambda c: c + [("Stock sale", "Test Co", "TEST.US", "STOCK", datetime(2026, 10, 1), 10.0, "1100",
                     "CLOSE SELL 1 @ 10", "My Trades", "9001")], "unknown type 'Stock sale'"),
    (lambda c: [r if r[6] != "1003" else (*r[:5], -201.0, *r[6:]) for r in c], "quantity x price"),
    (lambda c: [r if r[6] != "1003" else (*r[:7], "BUY 2 @ 100", *r[8:]) for r in c], "not understood"),
    (lambda c: [r if r[6] != "1004" else (*r[:6], "1003", *r[7:]) for r in c], "repeated ID"),
    (lambda c: [r if r[6] != "1001" else (*r[:5], -100.0, *r[6:]) for r in c], "Deposit with amount"),
    (lambda c: [r if r[6] != "1002" else (*r[:7], CONV.replace("PLN to USD", "PLN to EUR"), *r[8:]) for r in c],
     "not a currency conversion"),
])
def test_fails_closed(tmp_path, change, message):
    with pytest.raises(ParseError, match=message):
        parse_statement(build(tmp_path / "a.xlsx", cash=change(list(CASH))), ACCOUNT, ref(), WARSAW)


def test_total_account_currency_and_closed_positions(tmp_path):
    with pytest.raises(ParseError, match="Total"):
        parse_statement(build(tmp_path / "a.xlsx", total=999.0), ACCOUNT, ref(), WARSAW)
    with pytest.raises(ParseError, match="does not match export_identifier"):
        parse_statement(build(tmp_path / "b.xlsx", number="00000009"), ACCOUNT, ref(), WARSAW)
    with pytest.raises(ParseError, match="account currency is USD"):
        parse_statement(build(tmp_path / "c.xlsx", currency="EUR"), ACCOUNT, ref(), WARSAW)
    with pytest.raises(ParseError, match="sales are not supported"):
        parse_statement(build(tmp_path / "d.xlsx", closed=[("Test Co", "TEST.US")]), ACCOUNT, ref(), WARSAW)


def test_missing_alias_and_unchecked_number(tmp_path):
    with pytest.raises(ParseError) as exc:
        parse_statement(build(tmp_path / "a.xlsx"), ACCOUNT, Reference(), WARSAW)
    assert exc.value.missing_aliases == ["TEST.US"]
    no_id = Account("TOM-TEST-USD", "xtb", "tom", "", "Test", "brokerage", "USD", True, "parser")
    _, notes = parse_statement(build(tmp_path / "b.xlsx"), no_id, ref(), WARSAW)
    assert "account number not checked" in notes[0]


def test_not_an_xtb_file(tmp_path):
    (tmp_path / "x.xlsx").write_bytes(b"not a zip")
    with pytest.raises(ParseError, match="cannot read"):
        parse_statement(tmp_path / "x.xlsx", ACCOUNT, ref(), WARSAW)


def test_import_command(tmp_path, capsys):
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        f"TOM-TEST-USD,xtb,tom,{NUMBER},Test USD,brokerage,USD,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text("ticker,name,isin\nNYSE:TEST,Test,US0000000001\n", encoding="utf-8")
    (data / "instrument_aliases.csv").write_text("institution,alias,ticker\nxtb,TEST.US,NYSE:TEST\n", encoding="utf-8")
    folder = exports / "2026-11" / "TOM-TEST-USD"
    folder.mkdir(parents=True)
    build(folder / f"USD_{NUMBER}_2026-01-01_2026-11-03.xlsx")
    (folder / "notes.csv").write_text("x", encoding="utf-8")
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    out = capsys.readouterr().out
    assert "added 8, already known 0 (xtb.statement)" in out and "only .xlsx is supported" in out
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert rows[0]["key"] == "xtb|TOM-TEST-USD|1001"
    assert (folder / "_zaimportowane" / f"USD_{NUMBER}_2026-01-01_2026-11-03.xlsx").exists()


def _zip_setup(tmp_path, numbers=(NUMBER,)):
    """Config + accounts for one USD account per number; returns (cfg, data, month folder)."""
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    rows = "".join(f"TOM-TEST-{i},xtb,tom,{n},Test {i},brokerage,USD,TRUE,parser\n" for i, n in enumerate(numbers))
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n" + rows,
        encoding="utf-8")
    (data / "instruments.csv").write_text("ticker,name,isin\nNYSE:TEST,Test,US0000000001\n", encoding="utf-8")
    (data / "instrument_aliases.csv").write_text("institution,alias,ticker\nxtb,TEST.US,NYSE:TEST\n", encoding="utf-8")
    month = exports / "2026-11"
    month.mkdir(parents=True)
    return cfg, data, month


def _zip(path, tmp_path, members):
    """members: {name in zip: account number in the statement}."""
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        for i, (name, number) in enumerate(members.items()):
            src = build(tmp_path / f"m{i}.xlsx", number=number)
            zf.write(src, name)
    return path


def test_zip_import(tmp_path, capsys):
    second = "00000003"
    cfg, data, month = _zip_setup(tmp_path, (NUMBER, second))
    _zip(month / "statements_1234567890.zip", tmp_path, {
        f"{NUMBER}/USD_{NUMBER}_2026-01-01_2026-11-03.xlsx": NUMBER,
        f"{second}/USD_{second}_2026-01-01_2026-11-03.xlsx": second})
    assert main(["--config", str(cfg), "transactions", "import", "2026-11", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("add 8,") == 2 and "1234567890" not in out and NUMBER not in out
    assert (month / "statements_1234567890.zip").exists()
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert {r["account_id"] for r in rows} == {"TOM-TEST-0", "TOM-TEST-1"} and len(rows) == 16
    assert (month / "_zaimportowane" / "statements_1234567890.zip").exists()
    assert not (month / "statements_1234567890.zip").exists()


@pytest.mark.parametrize("members, message", [
    ({"00000009/USD_00000009.xlsx": "00000009"}, "no xtb account"),
    ({f"00000009/USD_{NUMBER}.xlsx": NUMBER}, "folder name"),
])
def test_zip_problems_keep_the_zip(tmp_path, capsys, members, message):
    cfg, data, month = _zip_setup(tmp_path)
    _zip(month / "s.zip", tmp_path, members)
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) != 0
    out = capsys.readouterr().out
    assert message in out and "kept in place" in out
    assert (month / "s.zip").exists() and not (data / "transactions.csv").exists()


def test_zip_partial_import_keeps_the_zip(tmp_path, capsys):
    second = "00000003"
    cfg, data, month = _zip_setup(tmp_path, (NUMBER, second))
    _zip(month / "s.zip", tmp_path, {f"{NUMBER}/a.xlsx": NUMBER, f"{second}/b.xlsx": second})
    assert main(["--config", str(cfg), "transactions", "import", "2026-11", "--account", "TOM-TEST-0"]) == 0
    out = capsys.readouterr().out
    assert "kept in place" in out and (month / "s.zip").exists()
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert {r["account_id"] for r in rows} == {"TOM-TEST-0"}


def test_zip_not_readable(tmp_path, capsys):
    cfg, data, month = _zip_setup(tmp_path)
    (month / "bad.zip").write_bytes(b"not a zip")
    main(["--config", str(cfg), "transactions", "import", "2026-11"])
    assert "cannot read the ZIP" in capsys.readouterr().out
