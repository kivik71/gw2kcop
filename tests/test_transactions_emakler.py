"""mBank eMakler exports (preamble, repeated header names, Windows-1250); synthetic data only."""

import csv
import io
from dataclasses import replace
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
ACCOUNT = Account("OWNA-TEST-IKZE", "mbank_brokerage", "owna", "", "Test IKZE", "ikze", "PLN", True, "parser")
PREAMBLE = [
    "Test Bank S.A.", "Skrytka Pocztowa 1", "", "eMAKLER - Historia transakcji", "",
    "#Imię i nazwisko", "TEST OSOBA", "", "#Nr rachunku mBM", "00000000", "",
    "#Data", "12.11.2026", "", "", "Kryteria wyszukiwania", "#Data od", "01.01.2026",
]
TRADES_HEADER = "Czas transakcji;Papier;Giełda;K/S;Liczba;Kurs;Waluta;Prowizja;Waluta;Wartość;Waluta"
CASH_HEADER = "Data;Opis;Kwota"

TRADES = "\n".join([*PREAMBLE, "", TRADES_HEADER,
    "19.06.2026 16:59:31;TEST ETF;DEU-XETRA;K;20;10,00;EUR;5,00;PLN;850,00;PLN",
    "18.06.2026 17:00:00;TESTCO;WWA-GPW;S;40;100,00;PLN;16,00;PLN;4 000,00;PLN",
    ""])
CASH = "\n".join([*PREAMBLE, "", CASH_HEADER,
    "15.10.2026 13:04:34;Dywidenda: DP: 2026-10-08 PLTEST000016 (TESTCO);;251,34",
    "19.08.2026 11:21:31;Dywidenda z 53 PW: DKTEST000012 DP: 2026-08-17 stawka brutto w wal.: 3.75 "
    "stawka pod.: 27% kurs przewalutowania: 0.5 |DVCA1;83,64",
    "22.06.2026 12:16:41;WYC: 1 NOT: 2 ZLC: 3 PW: PLTEST000016;507,43",
    "16.06.2026 15:07:17;Odblokowanie środków pod zlecenie nr 9 (całkowita realizacja);93,50",
    "16.06.2026 15:07:17;Odblokowanie środków pod zlecenie nr 8 (unieważnienie przez PMPLUS);10,00",
    "16.06.2026 15:05:18;Blokada środków pod zlecenie kupna nr 9;-4 751,68",
    "31.05.2026 23:37:18;IKZE WPŁATA                         ;804,00",
    "04.05.2026 07:06:05;WYC.BK: 1 POZ: 2 IKZE WPŁATA;5 000,00",
    ""])


def spec(kind):
    return next(s for s in builtin_specs() if s.name == f"mbank_brokerage.emakler_{kind}")


def ref():
    r = Reference()
    for ticker, isin in (("WSE:TESTCO", "PLTEST000016"), ("ETR:TEST", "DETEST000012"), ("CPH:TEST", "DKTEST000012")):
        r.tickers[ticker] = {"ticker": ticker, "isin": isin}
    for alias, ticker in (("TESTCO", "WSE:TESTCO"), ("TEST ETF", "ETR:TEST"),
                          ("PLTEST000016", "WSE:TESTCO"), ("DKTEST000012", "CPH:TEST")):
        r.aliases[("mbank_brokerage", alias)] = ticker
    return r


def test_trades_after_preamble_with_repeated_currency_columns():
    buy, sell = parse_export(TRADES, spec("trades"), ACCOUNT, ref(), WARSAW)
    assert (buy.type, buy.quantity, buy.price, buy.price_currency) == ("buy", Decimal(20), Decimal("10.00"), "EUR")
    assert (buy.amount, buy.currency, buy.fee) == (Decimal("-855.00"), "PLN", Decimal("5.00"))
    assert buy.instrument == "ETR:TEST" and buy.date == date(2026, 6, 19)
    assert (sell.quantity, sell.amount) == (Decimal(-40), Decimal("3984.00"))


def test_same_currency_value_is_checked():
    bad = TRADES.replace("4 000,00;PLN", "4 000,10;PLN")
    with pytest.raises(ParseError, match="quantity x price"):
        parse_export(bad, spec("trades"), ACCOUNT, ref(), WARSAW)


def test_fee_in_other_currency_stops():
    bad = TRADES.replace("5,00;PLN;850,00", "5,00;EUR;850,00")
    with pytest.raises(ParseError, match="fee in EUR on a trade settled in PLN"):
        parse_export(bad, spec("trades"), ACCOUNT, ref(), WARSAW)


def test_cash_rules_overflow_and_ignored_rows():
    txs = parse_export(CASH, spec("cash"), ACCOUNT, ref(), WARSAW)
    assert [t.type for t in txs] == ["dividend", "dividend", "deposit", "deposit"]
    div, foreign, dep, bank = txs
    assert div.description.endswith("(TESTCO);") and div.amount == Decimal("251.34")
    assert div.group_id == "mbank_brokerage|OWNA-TEST-IKZE|div:PLTEST000016:2026-10-08"
    assert foreign.isin == "DKTEST000012" and foreign.instrument == "CPH:TEST"
    assert dep.description == "IKZE WPŁATA" and dep.date == date(2026, 5, 31)
    assert bank.amount == Decimal("5000.00") and bank.currency == "PLN"


@pytest.mark.parametrize("line", [
    "16.06.2026 15:07:17;Odblokowanie środków pod zlecenie nr 9;93,50",
    "16.06.2026 15:05:18;Blokada środków pod zlecenie sprzedaży nr 9;-1,00",
    "16.06.2026 15:05:18;Nieznana opłata;-1,00",
])
def test_unknown_cash_text_fails_closed(line):
    bad = "\n".join([*PREAMBLE, CASH_HEADER, line, ""])
    with pytest.raises(ParseError, match="unknown operation text"):
        parse_export(bad, spec("cash"), ACCOUNT, ref(), WARSAW)


def test_zero_amount_without_direction_fails():
    bad = "\n".join([CASH_HEADER, "16.06.2026 15:05:18;IKZE WPŁATA;0,00", ""])
    with pytest.raises(ParseError, match="amount 0"):
        parse_export(bad, spec("cash"), ACCOUNT, ref(), WARSAW)


def test_header_required_and_preamble_only_where_allowed():
    with pytest.raises(ParseError, match="not found"):
        parse_export("\n".join(PREAMBLE), spec("cash"), ACCOUNT, ref(), WARSAW)
    no_preamble = replace(spec("cash"), preamble=False)
    with pytest.raises(ParseError, match="header does not match"):
        parse_export(CASH, no_preamble, ACCOUNT, ref(), WARSAW)


def test_repeated_header_name_must_be_numbered():
    data = {"kind": "trades", "decimal": ",", "datetime_format": "DD.MM.YYYY",
            "header": ["A", "Waluta", "Waluta"], "sides": {"K": "buy"},
            "columns": {"instrument": "A", "side": "A", "quantity": "A", "price": "A",
                        "price_currency": "Waluta", "gross": "A", "time": "A", "currency": "Waluta[2]"}}
    with pytest.raises(FormatError, match=r"repeated name"):
        parse_spec(data, "x.toml")
    data["columns"]["price_currency"] = "Waluta[1]"
    assert parse_spec(data, "x.toml").keys == ("A", "Waluta[1]", "Waluta[2]")


def test_import_detects_cp1250_files_after_preamble(tmp_path, capsys):
    data, exports = tmp_path / "Dane", tmp_path / "Eksporty"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "OWNA-TEST-IKZE,mbank_brokerage,owna,,Test IKZE,ikze,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text(
        "ticker,name,isin\nWSE:TESTCO,Test,PLTEST000016\nETR:TEST,Test ETF,DETEST000012\n"
        "CPH:TEST,Test DK,DKTEST000012\n", encoding="utf-8")
    (data / "instrument_aliases.csv").write_text(
        "institution,alias,ticker\nmbank_brokerage,TESTCO,WSE:TESTCO\nmbank_brokerage,TEST ETF,ETR:TEST\n"
        "mbank_brokerage,PLTEST000016,WSE:TESTCO\nmbank_brokerage,DKTEST000012,CPH:TEST\n", encoding="utf-8")
    month = exports / "2026-11" / "OWNA-TEST-IKZE"
    month.mkdir(parents=True)
    (month / "eMAKLER_historia_transakcji 2026.Csv").write_bytes(TRADES.encode("cp1250"))
    (month / "eMAKLER_historia_finansowa 2026.Csv").write_bytes(CASH.encode("cp1250"))
    assert main(["--config", str(cfg), "transactions", "import", "2026-11"]) == 0
    out = capsys.readouterr().out
    assert "emakler_trades" in out and "emakler_cash" in out
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    assert sorted(r["type"] for r in rows) == ["buy", "deposit", "deposit", "dividend", "dividend", "sell"]


def test_broker_wording_is_not_taken_as_deposit():
    bad = "\n".join([CASH_HEADER, "16.06.2026 15:05:18;Zwrot prowizji za zlecenie nr 9;1,00", ""])
    with pytest.raises(ParseError, match="unknown operation text"):
        parse_export(bad, spec("cash"), ACCOUNT, ref(), WARSAW)
