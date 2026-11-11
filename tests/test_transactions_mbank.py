"""mBank brokerage exports through the TOML engine and the import command (synthetic data only)."""

import codecs
from dataclasses import replace
import csv
import io
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions.engine import ParseError, parse_export
from gw2kcop.transactions.formats import FormatError, builtin_specs, parse_spec
from gw2kcop.transactions.reference import Reference

WARSAW = ZoneInfo("Europe/Warsaw")
TRADES_HEADER = ("Walor;Giełda;K/S;Liczba;Kurs;Waluta;Wartość;Prowizja;Czas transakcji;"
                 "Waluta rozliczenia;Kurs Przewalutowania")
CASH_HEADER = "Data księgowania;Typ operacji;Opis;Kwota;Saldo początkowe;Saldo końcowe"
ACCOUNT = Account("TOM-TEST-IKE", "mbank_brokerage", "tom", "", "Test IKE", "ike", "PLN", True, "parser")


def spec(kind):
    return next(s for s in builtin_specs() if s.name == f"mbank_brokerage.{kind}")


def ref(**aliases):
    r = Reference()
    for alias, (ticker, isin) in aliases.items():
        r.tickers[ticker] = {"ticker": ticker, "isin": isin}
        r.aliases[("mbank_brokerage", alias.replace("_", " "))] = ticker
    return r


def export(header, rows):
    return codecs.BOM_UTF8.decode("utf-8") + "\r\n".join([header, *rows]) + "\r\n"


TRADES = export(TRADES_HEADER, [
    "TESTCO;WWA;Kupno;10;12,50000000;PLN;125,00;5,00;03.02.2026 16:43:38;PLN;",
    "TESTCO;WWA;Kupno;10;12,50000000;PLN;125,00;5,00;03.02.2026 16:43:38;PLN;",
    "TESTCO;WWA;Sprzedaż;4;20,00000000;PLN;80,00;5,00;29.05.2026 09:02:52;PLN;",
    "TEST GR ETF;DEU;Kupno;3;50,00000000;EUR;637,50;0,00;30.03.2026 23:30:00;PLN;4,2500",
])
CASH = export(CASH_HEADER, [
    '22.05.2026 00:00:00;Obciążenia;"Podatek od dywidendy: DP: 2026-05-15 PLTEST000016 (TESTCO);";-2,38;112,50;110,12',
    '22.05.2026 00:00:00;Uznania;"Dywidenda: DP: 2026-05-15 PLTEST000016 (TESTCO);";12,50;100,00;112,50',
    "10.05.2026 00:00:00;Uznania;WPŁATA NA IKE;100,00;0,00;100,00",
])
REF = ref(TESTCO=("WSE:TESTCO", "PLTEST000016"), TEST_GR_ETF=("ETR:TEST", "DETEST000012"),
          PLTEST000016=("WSE:TESTCO", "PLTEST000016"))


def test_builtin_specs_load():
    names = {s.name for s in builtin_specs()}
    assert {"mbank_brokerage.trades", "mbank_brokerage.cash"} <= names


def test_trades():
    txs = parse_export(TRADES, spec("trades"), ACCOUNT, REF, WARSAW)
    buy, _, sell, foreign = txs
    assert (buy.type, buy.quantity, buy.amount, buy.fee) == ("buy", Decimal(10), Decimal("-130.00"), Decimal("5.00"))
    assert buy.instrument == "WSE:TESTCO" and buy.isin == "PLTEST000016" and buy.price_currency == "PLN"
    assert buy.timestamp_utc.isoformat() == "2026-02-03T16:43:38+01:00" and buy.date == date(2026, 2, 3)
    assert (sell.type, sell.quantity, sell.amount) == ("sell", Decimal(-4), Decimal("75.00"))
    assert foreign.price_currency == "EUR" and foreign.currency == "PLN" and foreign.amount == Decimal("-637.50")
    assert foreign.date == date(2026, 3, 30)  # 23:30 Warsaw stays on the local day


def test_trade_value_check_fails_closed():
    bad = TRADES.replace("80,00;5,00", "81,00;5,00")
    with pytest.raises(ParseError, match="quantity x price"):
        parse_export(bad, spec("trades"), ACCOUNT, REF, WARSAW)


def test_fee_in_trade_currency_settled_same_currency():
    ok = export(TRADES_HEADER, ["TESTCO;LON;Kupno;1;10,00000000;USD;10,00;1,00;03.02.2026 10:00:00;USD;"])
    [tx] = parse_export(ok, spec("trades"), ACCOUNT, REF, WARSAW)
    assert (tx.amount, tx.fee, tx.currency) == (Decimal("-11.00"), Decimal("1.00"), "USD")


def test_fee_with_currency_conversion_is_in_settlement_currency():
    row = export(TRADES_HEADER, ["TESTCO;DEU;Kupno;1;10,00000000;EUR;42,50;1,00;03.02.2026 10:00:00;PLN;4,2500"])
    [tx] = parse_export(row, spec("trades"), ACCOUNT, REF, WARSAW)
    assert (tx.amount, tx.fee, tx.currency, tx.price_currency) == (Decimal("-43.50"), Decimal("1.00"), "PLN", "EUR")


def test_fee_only_same_currency_check_still_works():
    strict = replace(spec("trades"), checks={**spec("trades").checks, "fee_only_same_currency": True})
    bad = export(TRADES_HEADER, ["TESTCO;DEU;Kupno;1;10,00000000;EUR;42,50;1,00;03.02.2026 10:00:00;PLN;4,2500"])
    with pytest.raises(ParseError, match="fee currency not confirmed"):
        parse_export(bad, strict, ACCOUNT, REF, WARSAW)


def test_unknown_side_and_alias_are_reported_together():
    bad = export(TRADES_HEADER, [
        "TESTCO;WWA;Zamiana;1;1,00000000;PLN;1,00;0,00;03.02.2026 10:00:00;PLN;",
        "NEWCO;WWA;Kupno;1;1,00000000;PLN;1,00;0,00;03.02.2026 10:00:00;PLN;",
    ])
    with pytest.raises(ParseError) as exc:
        parse_export(bad, spec("trades"), ACCOUNT, REF, WARSAW)
    assert "unknown side" in str(exc.value) and exc.value.missing_aliases == ["NEWCO"]


def test_cash():
    tax, div, dep = parse_export(CASH, spec("cash"), ACCOUNT, REF, WARSAW)
    assert (div.type, div.amount, div.instrument, div.isin) == ("dividend", Decimal("12.50"), "WSE:TESTCO", "PLTEST000016")
    assert tax.type == "tax" and tax.amount == Decimal("-2.38")
    assert div.group_id == tax.group_id == "mbank_brokerage|TOM-TEST-IKE|div:PLTEST000016:2026-05-15"
    assert dep.type == "deposit" and dep.currency == "PLN" and dep.timestamp_utc is None


def test_cash_balance_chain_fails_closed():
    bad = CASH.replace("100,00;0,00;100,00", "100,00;0,00;100,01")
    with pytest.raises(ParseError, match="balance check"):
        parse_export(bad, spec("cash"), ACCOUNT, REF, WARSAW)


def test_unknown_debit_text_fails_closed():
    bad = export(CASH_HEADER, ["10.05.2026 00:00:00;Obciążenia;NIEZNANA OPERACJA;-1,00;1,00;0,00"])
    with pytest.raises(ParseError, match="unknown operation text"):
        parse_export(bad, spec("cash"), ACCOUNT, REF, WARSAW)


def test_spec_rejects_unknown_keys():
    with pytest.raises(FormatError, match="unknown keys"):
        parse_spec({"kind": "cash", "colour": "red"}, "x.toml")


# ------------------------------------------------------------------ CLI end to end


def write(path, text, bom=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8", newline="")


@pytest.fixture
def setup(tmp_path):
    data = tmp_path / "Dane"
    exports = tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    write(data / "accounts.csv",
          "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
          "TOM-TEST-IKE,mbank_brokerage,tom,,Test IKE,ike,PLN,TRUE,parser\n"
          "TOM-UFK-TEST,test_ufk,tom,,UFK,unit_linked,PLN,TRUE,manual\n")
    write(data / "instruments.csv", "ticker,name,isin\nWSE:TESTCO,Test,PLTEST000016\nETR:TEST,Test ETF,DETEST000012\n")
    write(data / "instrument_aliases.csv", "institution,alias,ticker\n"
          "mbank_brokerage,TESTCO,WSE:TESTCO\nmbank_brokerage,PLTEST000016,WSE:TESTCO\n")
    month = exports / "2026-05" / "TOM-TEST-IKE"
    write(month / "Historia transakcji 1.csv", TRADES.lstrip("﻿"), bom=True)
    write(month / "Historia operacji finansowych 2.csv", CASH.lstrip("﻿"), bom=True)
    write(exports / "2026-05" / "TOM-UFK-TEST" / "saldo.txt", "paste")
    return ["--config", str(cfg)], data, month


def test_import_reports_missing_alias_and_writes_nothing(setup, capsys):
    cfg, data, month = setup
    assert main([*cfg, "transactions", "import", "2026-05"]) == 1
    out = capsys.readouterr().out
    assert "mbank_brokerage,TEST GR ETF,<ticker>" in out
    assert (month / "Historia transakcji 1.csv").exists()          # failed file stays
    assert not (month / "Historia operacji finansowych 2.csv").exists()  # good file moved
    assert (month / "_zaimportowane" / "Historia operacji finansowych 2.csv").exists()
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert [r["type"] for r in rows] == ["tax", "dividend", "deposit"]


def test_import_dry_run_then_real_then_reimport(setup, capsys):
    cfg, data, month = setup
    with open(data / "instrument_aliases.csv", "a", encoding="utf-8") as f:
        f.write("mbank_brokerage,TEST GR ETF,ETR:TEST\n")
    assert main([*cfg, "transactions", "import", "2026-05", "--dry-run"]) == 0
    assert "would add 4" in capsys.readouterr().out
    assert not (data / "transactions.csv").exists()
    assert main([*cfg, "transactions", "import", "2026-05"]) == 0
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert len(rows) == 7
    keys = [r["key"] for r in rows if r["type"] == "buy"]
    assert len(keys) == 3 and keys[0].endswith("|1") and keys[1].endswith("|2")
    # the same files exported again next month add nothing
    again = month.parent.parent / "2026-06" / "TOM-TEST-IKE"
    write(again / "Historia transakcji 9.csv", TRADES.lstrip("﻿"), bom=True)
    assert main([*cfg, "transactions", "import", "2026-06"]) == 0
    assert "added 0, already known 4" in capsys.readouterr().out


def test_import_unknown_header(setup, capsys):
    cfg, _, month = setup
    write(month / "inny.csv", "a;b;c\n1;2;3\n")
    assert main([*cfg, "transactions", "import", "2026-05", "--account", "TOM-TEST-IKE"]) == 1
    assert "header not recognised" in capsys.readouterr().out
