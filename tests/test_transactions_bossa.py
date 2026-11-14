"""Bossa financeHistory.csv (trades described in the cash text); synthetic data only."""

import csv
import io
from dataclasses import replace
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions.engine import ParseError, parse_export
from gw2kcop.transactions.formats import FormatError, builtin_specs, parse_spec
from gw2kcop.transactions.reference import Reference

WARSAW = ZoneInfo("Europe/Warsaw")
ACCOUNT = Account("OWNA-TEST-IKE", "bossa", "owna", "IKE 00000001", "Test IKE", "ike", "PLN", True, "parser")
HEADER = "Data;Rachunek;Waluta;Tytuł operacji;Wartość"
ROWS = [
    "05.01.2026;IKE 00000001;PLN;Rozliczenie transakcji kupna: Test ETF  (IETEST000012) TETF 25.000000 x 100.000000 PLN nr Z00000000001;-2512,50",
    "05.01.2026;IKE 00000001;PLN;Przelew do DM BOŚ;5000,00",
    "15.01.2026;IKE 00000001;PLN;Wypłata dywidendy brutto TETF PLN;41,84",
    "02.02.2026;IKE 00000001;PLN;Rozliczenie transakcji kupna: TESTWIG (PLTEST000016) 3.000000 x 65.630000 PLN nr 000000000300;-197,38",
    "16.02.2026;IKE 00000001;PLN;Wypłata dywidendy TEXT;149,50",
    "08.04.2026;IKE 00000001;PLN;Rozliczenie transakcji sprzedaży: Test ETF  (IETEST000012) TETF 2.000000 x 2653.131730 PLN nr Z00000000002;5300,00",
    "26.08.2026;IKE 00000001;PLN;Wypłata dywidendy netto TCO 73% PLN;31,60",
    "30.10.2026;IKE 00000001;PLN;Wypłata dywidendy TCO;500,00",
]


def text(rows=ROWS):
    return "﻿" + "\n".join([HEADER, *rows, ""])


def spec():
    return next(s for s in builtin_specs() if s.name == "bossa.finance_history")


def ref():
    r = Reference()
    for ticker, isin in (("LSE:TETF", "IETEST000012"), ("WSE:TESTWIG", "PLTEST000016"), ("NYSE:TCO", "USTEST000018")):
        r.tickers[ticker] = {"ticker": ticker, "isin": isin}
    for alias, ticker in (("IETEST000012", "LSE:TETF"), ("TETF", "LSE:TETF"), ("PLTEST000016", "WSE:TESTWIG"),
                          ("TCO", "NYSE:TCO")):
        r.aliases[("bossa", alias)] = ticker
    return r


def test_finance_history():
    txs = parse_export(text(), spec(), ACCOUNT, ref(), WARSAW)
    assert [t.type for t in txs] == ["buy", "deposit", "dividend", "buy", "dividend", "sell", "dividend", "dividend"]
    buy, dep, div, wig, unknown, sell, net, plain = txs
    assert (buy.instrument, buy.isin, buy.quantity, buy.price, buy.fee, buy.amount) == (
        "LSE:TETF", "IETEST000012", Decimal(25), Decimal(100), Decimal("12.50"), Decimal("-2512.50"))
    assert buy.price_currency == "PLN" and buy.date.isoformat() == "2026-01-05" and buy.timestamp_utc is None
    assert wig.instrument == "WSE:TESTWIG" and wig.fee == Decimal("0.49")  # no ticker after the ISIN
    # 2 x 2653.131730 = 5306.26 after rounding; the sale paid 5300.00
    assert (sell.quantity, sell.fee, sell.amount) == (Decimal(-2), Decimal("6.26"), Decimal("5300.00"))
    assert (div.instrument, div.isin) == ("LSE:TETF", "IETEST000012")
    assert unknown.instrument == "" and unknown.amount == Decimal("149.50")
    assert net.instrument == plain.instrument == "NYSE:TCO" and net.isin == "USTEST000018"
    assert dep.amount == Decimal(5000) and dep.fee is None


@pytest.mark.parametrize("row, message", [
    ("05.01.2026;IKE 00000002;PLN;Przelew do DM BOŚ;1,00", "export_identifier"),
    ("05.01.2026;IKE 00000001;PLN;Wypłata środków;-1,00", "unknown operation text"),
    ("05.01.2026;IKE 00000001;PLN;Rozliczenie transakcji kupna: X (IETEST000012) 1.000000 x 10.000000 PLN nr 1;-9,99",
     "fee would be negative"),
    ("05.01.2026;IKE 00000001;PLN;Rozliczenie transakcji kupna: X (IETEST000012) 1.000000 x 10.000000 EUR nr 1;-43,00",
     "price in EUR"),
    ("05.01.2026;IKE 00000001;PLN;Rozliczenie transakcji kupna: X (IETEST000012) 1.000000 x 10.000000 PLN nr 1;10,00",
     "unknown operation text"),  # a buy that credits cash
])
def test_fails_closed(row, message):
    with pytest.raises(ParseError, match=message):
        parse_export(text([row]), spec(), ACCOUNT, ref(), WARSAW)


def test_account_check_skipped_without_export_identifier():
    other = replace(ACCOUNT, export_identifier="")
    rows = ["05.01.2026;IKE 00000002;PLN;Przelew do DM BOŚ;1,00"]
    assert len(parse_export(text(rows), spec(), other, ref(), WARSAW)) == 1


def test_missing_aliases_are_listed():
    with pytest.raises(ParseError) as exc:
        parse_export(text(), spec(), ACCOUNT, Reference(), WARSAW)
    assert exc.value.missing_aliases == ["IETEST000012", "PLTEST000016", "TCO", "TETF"]


@pytest.mark.parametrize("rule, message", [
    ({"pattern": "^K (?P<isin>X) (?P<quantity>1)$", "type": "buy"}, "must capture quantity, price"),
    ({"pattern": "^K (?P<isin>X) (?P<quantity>1) (?P<price>1)$", "type": "buy", "direction": "credit"}, "is a debit"),
    ({"pattern": "^D (?P<isin>X) (?P<alias>Y)$", "type": "dividend"}, "not both"),
])
def test_trade_rule_validation(rule, message):
    data = {"format": "t", "institution": "t", "kind": "cash", "decimal": ",", "datetime_format": "DD.MM.YYYY",
            "header": ["Data", "Opis", "Kwota"], "columns": {"date": "Data", "description": "Opis", "amount": "Kwota"},
            "currency": "account", "rules": [rule]}
    with pytest.raises(FormatError, match=message):
        parse_spec(data, "t.toml")


def test_import_command(tmp_path, capsys):
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "OWNA-TEST-IKE,bossa,owna,IKE 00000001,Test IKE,ike,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text(
        "ticker,name,isin\nLSE:TETF,Test,IETEST000012\nWSE:TESTWIG,Test,PLTEST000016\nNYSE:TCO,Test,USTEST000018\n",
        encoding="utf-8")
    (data / "instrument_aliases.csv").write_text(
        "institution,alias,ticker\nbossa,IETEST000012,LSE:TETF\nbossa,TETF,LSE:TETF\n"
        "bossa,PLTEST000016,WSE:TESTWIG\nbossa,TCO,NYSE:TCO\n", encoding="utf-8")
    folder = exports / "2026-11" / "OWNA-TEST-IKE"
    folder.mkdir(parents=True)
    (folder / "financeHistory.csv").write_bytes(text().encode("utf-8"))
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    assert "added 8, already known 0 (bossa.finance_history)" in capsys.readouterr().out
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert rows[0]["type"] == "buy" and rows[0]["fee"] == "12.5" and rows[0]["key"].startswith("bossa|OWNA-TEST-IKE|h:")
    (folder / "financeHistory.csv").write_bytes(text().encode("utf-8"))
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    assert "added 0, already known 8" in capsys.readouterr().out
