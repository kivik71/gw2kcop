"""Erste Brokerage trade report and cash history; synthetic data only."""

import csv
import io
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions.engine import ParseError, parse_export
from gw2kcop.transactions.formats import builtin_specs
from gw2kcop.transactions.reference import Reference

WARSAW = ZoneInfo("Europe/Warsaw")
ACCOUNT = Account("OWNA-ERSTE-BM", "erste_brokerage", "owna", "", "Test", "brokerage", "PLN", True, "parser")
TRADES_HEADER = ("Czas zawarcia;Walor;Giełda;Waluta notowania;Oferta;Ilość;Kurs rozl.;Waluta Kurs rozl.;"
                 "Wartość;Waluta Wartość;Prowizja;Waluta Prowizja;Numer transakcji;Status zlecenia;"
                 "Data rozliczenia;Id zlecenia;Kwota nieopłacona;Waluta Kwota nieopłacona;Kurs przewalutowania")
TRADES = [
    "15-05-2026 11:00:00;TESTA;WWA;PLN;Sprzedaż;4;0,625;PLN;2,50;PLN;5,00;PLN;Z1;Zrealizowane;19-05-2026;101;;PLN;",
    "11-05-2026 12:21:43;TESTB;WWA;PLN;Sprzedaż;5;0,145;PLN;0,73;PLN;5,00;PLN;Z2;Zrealizowane;13-05-2026;102;;PLN;",
]
TOTAL = ";;;;;9;0,77;;3,23;PLN;10,00;PLN;;;;;;;"
CASH_HEADER = "Data operacji;Typ operacji;Kwota;Waluta Kwota;Opis;Saldo po operacji;Waluta Saldo po operacji;Id operacji"
CASH = [
    '22-05-2026;Obciążenie;-484,00;PLN;"Podatek od dywidendy: DP: 2026-05-14 PLTEST000016 (TESTA);";2139,77;PLN;9004',
    '22-05-2026;Uznanie;2544,75;PLN;"Dywidenda: DP: 2026-05-14 PLTEST000016 (TESTA);";2623,77;PLN;9003',
    "12-05-2026;Uznanie;100,00;PLN;NA PORZĄDKI;86,10;PLN;9002",
    "11-05-2026;Obciążenie;-5,00;PLN;NOT: 1 ZLC: 102 PW: PLTEST000024;-13,90;PLN;9001",
    "11-05-2026;Uznanie;0,73;PLN;NOT: 1 ZLC: 102 PW: PLTEST000024;-8,90;PLN;9000",
]


def text(header, rows):
    return "﻿" + "\r\n".join([header, *rows, ""])


def spec(name):
    return next(s for s in builtin_specs() if s.name == f"erste_brokerage.{name}")


def ref():
    r = Reference()
    for ticker, isin in (("WSE:TESTA", "PLTEST000016"), ("WSE:TESTB", "PLTEST000024")):
        r.tickers[ticker] = {"ticker": ticker, "isin": isin}
    for alias, ticker in (("TESTA", "WSE:TESTA"), ("TESTB", "WSE:TESTB"),
                          ("PLTEST000016", "WSE:TESTA"), ("PLTEST000024", "WSE:TESTB")):
        r.aliases[("erste_brokerage", alias)] = ticker
    return r


def test_trade_report():
    a, b = parse_export(text(TRADES_HEADER, [*TRADES, TOTAL]), spec("trades"), ACCOUNT, ref(), WARSAW)
    assert (a.type, a.instrument, a.isin, a.quantity, a.price, a.fee, a.amount) == (
        "sell", "WSE:TESTA", "PLTEST000016", Decimal(-4), Decimal("0.625"), Decimal("5.00"), Decimal("-2.50"))
    assert a.timestamp_utc.astimezone(ZoneInfo("UTC")).hour == 9  # 11:00 Warsaw (CEST)
    assert b.amount == Decimal("-4.27")  # 0.73 rounded value minus the 5.00 fee


@pytest.mark.parametrize("rows, message", [
    (TRADES, "summary row"),                                            # no summary row
    ([TRADES[0], TOTAL], "summary row: Wartość"),                       # sums differ
    ([TRADES[0].replace("Zrealizowane", "Anulowane"), TOTAL.replace("3,23", "2,50").replace("10,00", "5,00")],
     "only 'Zrealizowane'"),
    ([TRADES[0].replace("Sprzedaż", "Kupno"), TOTAL.replace("3,23", "2,50").replace("10,00", "5,00")],
     "unknown side"),                                                   # buys not sampled yet
])
def test_trade_report_fails_closed(rows, message):
    with pytest.raises(ParseError, match=message):
        parse_export(text(TRADES_HEADER, rows), spec("trades"), ACCOUNT, ref(), WARSAW)


def test_cash_history():
    tax, div, dep = parse_export(text(CASH_HEADER, CASH), spec("cash"), ACCOUNT, ref(), WARSAW)
    assert (tax.type, tax.amount, tax.isin, tax.instrument) == ("tax", Decimal("-484.00"), "PLTEST000016", "WSE:TESTA")
    assert div.group_id == tax.group_id == "erste_brokerage|OWNA-ERSTE-BM|div:PLTEST000016:2026-05-14"
    assert (dep.type, dep.source_id, dep.amount) == ("deposit", "9002", Decimal("100.00"))


@pytest.mark.parametrize("row", [
    "12-05-2026;Obciążenie;-10,00;PLN;Opłata za rachunek;0,00;PLN;9010",   # unknown debit
    "12-05-2026;Uznanie;1,00;PLN;Odsetki;0,00;PLN;9011",                   # broker word, not a deposit
    "12-05-2026;Uznanie;-1,00;PLN;NA PORZĄDKI;0,00;PLN;9012",              # direction vs sign
])
def test_cash_fails_closed(row):
    with pytest.raises(ParseError):
        parse_export(text(CASH_HEADER, [row]), spec("cash"), ACCOUNT, ref(), WARSAW)


def test_import_command(tmp_path, capsys):
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "OWNA-ERSTE-BM,erste_brokerage,owna,,Test,brokerage,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text(
        "ticker,name,isin\nWSE:TESTA,Test,PLTEST000016\nWSE:TESTB,Test,PLTEST000024\n", encoding="utf-8")
    (data / "instrument_aliases.csv").write_text(
        "institution,alias,ticker\nerste_brokerage,TESTA,WSE:TESTA\nerste_brokerage,TESTB,WSE:TESTB\n"
        "erste_brokerage,PLTEST000016,WSE:TESTA\n", encoding="utf-8")
    folder = exports / "2026-11" / "OWNA-ERSTE-BM"
    folder.mkdir(parents=True)
    (folder / "Raport_transakcji_1.csv").write_bytes(text(TRADES_HEADER, [*TRADES, TOTAL]).encode("utf-8"))
    (folder / "Historia_operacji_1.csv").write_bytes(text(CASH_HEADER, CASH).encode("utf-8"))
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    out = capsys.readouterr().out
    assert "added 2, already known 0 (erste_brokerage.trades)" in out
    assert "added 3, already known 0 (erste_brokerage.cash)" in out
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert "erste_brokerage|OWNA-ERSTE-BM|9002" in {r["key"] for r in rows}
