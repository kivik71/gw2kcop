"""PPK histories: NN (.xls fixture) and ING (.csv built in the test). Synthetic data only."""

import csv
import io
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gw2kcop.accounts import Account
from gw2kcop.cli import main
from gw2kcop.transactions import ppk
from gw2kcop.transactions.engine import ParseError
from gw2kcop.transactions.reference import Reference

FIXTURE = Path(__file__).parent / "fixtures" / "ppk_nn_history.xls"
NN = Account("OWNA-PPK-NN", "nn", "owna", "00000001", "Test NN", "ppk", "PLN", True, "parser")
ING = Account("OWNB-PPK-ING", "ing", "ownb", "00000002", "Test ING", "ppk", "PLN", True, "parser")
OLD, NEW, ING_FUND = "Test PPK Fund 2035", "Test PPK Fund 2050", "Test ING Fund 2045"


def ref():
    r = Reference()
    for ticker, inst, alias in (("PPK:T2035", "nn", OLD), ("PPK:T2050", "nn", NEW), ("PPK:I2045", "ing", ING_FUND)):
        r.tickers[ticker] = {"ticker": ticker, "isin": ""}
        r.aliases[(inst, alias)] = ticker
    return r


def nn_rows():
    import xlrd

    sheet = xlrd.open_workbook(str(FIXTURE)).sheet_by_index(0)
    return [sheet.row_values(r) for r in range(sheet.nrows)]


# ---------------------------------------------------------------- NN


def test_nn_payments_become_payment_and_purchase():
    txs, notes = ppk.parse_nn(FIXTURE, NN, ref())
    assert notes == []
    assert sum(t.amount for t in txs) == 0  # a PPK account holds no cash
    first = [t for t in txs if t.date == date(2024, 7, 2)]
    assert [t.type for t in first] == ["ppk_employee", "buy", "ppk_employer", "buy"] or \
        [t.type for t in first] == ["ppk_employer", "buy", "ppk_employee", "buy"]
    pay = next(t for t in first if t.type == "ppk_employee")
    buy = next(t for t in first if t.type == "buy" and t.group_id == pay.group_id)
    assert pay.amount == Decimal("300.00") and pay.instrument == "" and pay.quantity is None
    assert (buy.amount, buy.quantity, buy.price, buy.instrument) == (Decimal("-300.00"), Decimal(30), Decimal(10),
                                                                     "PPK:T2035")
    assert pay.description == "Wpłata podstawowa pracownika 06-2024 (zlecenie 2024-06-28)"
    state = [t.type for t in txs if t.type == "ppk_state"]
    assert len(state) == 2


def test_nn_fund_switch_is_two_rows():
    txs, _ = ppk.parse_nn(FIXTURE, NN, ref())
    switch = [t for t in txs if t.type == "fund_switch"]
    assert len(switch) == 2 and switch[0].group_id == switch[1].group_id
    out = next(t for t in switch if t.instrument == "PPK:T2035")
    new = next(t for t in switch if t.instrument == "PPK:T2050")
    assert (out.quantity, out.amount) == (Decimal(-80), Decimal("1000.00"))
    assert (new.quantity, new.amount) == (Decimal(50), Decimal("-1000.00"))
    assert out.date == date(2025, 10, 6)  # valuation date


def test_nn_unknown_fund_lists_missing_alias():
    with pytest.raises(ParseError) as exc:
        ppk.parse_nn(FIXTURE, NN, Reference())
    assert exc.value.missing_aliases == [OLD, NEW]


def test_nn_wrong_account_number():
    other = Account("OWNA-PPK-NN", "nn", "owna", "00000009", "Test NN", "ppk", "PLN", True, "parser")
    with pytest.raises(ParseError, match="does not match export_identifier"):
        ppk.parse_nn(FIXTURE, other, ref())


def test_nn_unknown_type_stops_the_file():
    rows = nn_rows()
    rows[5][2] = "Wypłata transferowa"
    with pytest.raises(ParseError, match="unknown transaction type"):
        ppk.parse_nn_rows(rows, NN, ref())


def test_nn_units_far_from_amount_and_tax_stop_the_file():
    rows = nn_rows()
    rows[5][8] = 6.0  # 6 x 20 != 100
    with pytest.raises(ParseError, match="far from the amount"):
        ppk.parse_nn_rows(rows, NN, ref())
    rows = nn_rows()
    rows[5][10] = 1.0
    with pytest.raises(ParseError, match="tax"):
        ppk.parse_nn_rows(rows, NN, ref())


def test_nn_switch_must_net_to_zero():
    rows = nn_rows()
    for row in rows:
        if row[2] == "Zmiana funduszu (nabycie)":
            row[4] = row[9] = 999.0
            row[8] = 49.95
    with pytest.raises(ParseError, match="do not net to zero"):
        ppk.parse_nn_rows(rows, NN, ref())


def test_nn_not_an_xls(tmp_path):
    path = tmp_path / "a.xls"
    path.write_text("not excel")
    with pytest.raises(ParseError, match="cannot read as .xls"):
        ppk.parse_nn(path, NN, ref())


# ---------------------------------------------------------------- ING


def ing_text(rows=None, number="00000002"):
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Transaction history"])
    w.writerow(["File created", "3.11.2026, 18:39"])
    w.writerow([])
    w.writerow(["Participant details"])
    w.writerow(["SYNTHETIC TEST PERSON"])
    w.writerow([])
    w.writerow(["Participant numbers"])
    w.writerow([f"{number} - PPK - TEST EMPLOYER"])
    w.writerow([])
    w.writerow([])
    w.writerow(["Selected period", "from 01.01.2026 to 31.10.2026"])
    w.writerow([])
    w.writerow(ppk.ING_HEADER)
    product = "PPK - TEST EMPLOYER"
    for row in rows if rows is not None else [
        ("13.10.2026", "Complete", "Purchase", "PPK Primary employee's payment", "PPK", product, ING_FUND,
         "1 000,00", "40,000000", "A"),
        ("13.10.2026", "Complete", "Purchase", "PPK Primary employer's payment", "PPK", product, ING_FUND,
         "375,00", "15,000000", "A"),
        ("27.03.2026", "Complete", "Purchase", "PPK Annual payment", "PPK", product, ING_FUND,
         "240,00", "12,000000", "A"),
    ]:
        w.writerow(row)
    return "﻿" + out.getvalue().rstrip("\n")


def test_ing_payments():
    txs, notes = ppk.parse_ing_text(ing_text(), ING, ref())
    assert notes == []
    assert [t.type for t in txs[:2]] == ["ppk_state", "buy"]
    assert sum(t.amount for t in txs) == 0
    emp = next(t for t in txs if t.type == "ppk_employee")
    buy = next(t for t in txs if t.type == "buy" and t.group_id == emp.group_id)
    assert emp.amount == Decimal("1000.00") and emp.date == date(2026, 10, 13)
    assert (buy.quantity, buy.price, buy.instrument) == (Decimal("40.000000"), Decimal("25.000000"), "PPK:I2045")


def test_ing_checks():
    with pytest.raises(ParseError, match="does not match export_identifier"):
        ppk.parse_ing_text(ing_text(number="00000009"), ING, ref())
    bad = [("13.10.2026", "Pending", "Purchase", "PPK Primary employee's payment", "PPK", "x", ING_FUND,
            "1,00", "1,000000", "A")]
    with pytest.raises(ParseError, match="only Complete Purchase"):
        ppk.parse_ing_text(ing_text(bad), ING, ref())
    bad = [("13.10.2026", "Complete", "Purchase", "PPK Withdrawal", "PPK", "x", ING_FUND, "1,00", "1,000000", "A")]
    with pytest.raises(ParseError, match="unknown transaction type"):
        ppk.parse_ing_text(ing_text(bad), ING, ref())
    with pytest.raises(ParseError, match="not an ING PPK"):
        ppk.parse_ing_text("a,b\n", ING, ref())


def test_ing_overlapping_files_give_the_same_keys():
    from gw2kcop.transactions.master import assign_keys

    a, _ = ppk.parse_ing_text(ing_text(), ING, ref())
    b, _ = ppk.parse_ing_text(ing_text(), ING, ref())
    assert [k for k, _ in assign_keys(a)] == [k for k, _ in assign_keys(b)]
    assert len({k for k, _ in assign_keys(a)}) == len(a)


# ---------------------------------------------------------------- CLI


def _setup(tmp_path):
    data = tmp_path / "Dane"
    exports = tmp_path / "Eksporty"
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "OWNA-PPK-NN,nn,owna,00000001,Test NN,ppk,PLN,TRUE,parser\n"
        "OWNB-PPK-ING,ing,ownb,00000002,Test ING,ppk,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text("ticker,isin\nPPK:T2035,\nPPK:T2050,\nPPK:I2045,\n", encoding="utf-8")
    (data / "instrument_aliases.csv").write_text(
        f"institution,alias,ticker\nnn,{OLD},PPK:T2035\nnn,{NEW},PPK:T2050\ning,{ING_FUND},PPK:I2045\n",
        encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\n'
                      f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                      encoding="utf-8")
    month = exports / "2026-11"
    (month / "OWNA-PPK-NN").mkdir(parents=True)
    (month / "OWNB-PPK-ING").mkdir(parents=True)
    shutil.copy(FIXTURE, month / "OWNA-PPK-NN" / "Historia.xls")
    (month / "OWNB-PPK-ING" / "transaction-history.csv").write_text(ing_text(), encoding="utf-8")
    return config, data


def test_cli_imports_both(tmp_path, capsys):
    config, data = _setup(tmp_path)
    assert main(["--config", str(config), "transactions", "import", "2026-11"]) == 0
    out = capsys.readouterr().out
    assert "nn.ppk_history" in out and "ing.ppk_history" in out
    with open(data / "transactions.csv", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 6 * 2 + 2 + 3 * 2  # NN: 6 payments + 1 switch; ING: 3 payments
    assert sum(Decimal(r["amount"]) for r in rows) == 0
    assert main(["--config", str(config), "transactions", "import", "2026-11"]) == 0
    assert "No export files found" in capsys.readouterr().out  # files moved to _zaimportowane/


def test_national_fund_code_is_not_used_as_isin():
    r = ref()
    r.tickers["PPK:T2035"]["isin"] = "RFe 40"  # a national fund code, not an ISIN
    r.tickers["PPK:T2050"]["isin"] = "PL0000000001"
    txs, _ = ppk.parse_nn(FIXTURE, NN, r)
    assert {t.isin for t in txs if t.instrument == "PPK:T2035"} == {""}
    assert {t.isin for t in txs if t.instrument == "PPK:T2050"} == {"PL0000000001"}


def test_ing_welcome_payment_is_state_money():
    rows = [("27.07.2025", "Complete", "Purchase", "PPK Welcome payment", "PPK", "x", ING_FUND,
             "250,00", "10,000000", "A")]
    txs, _ = ppk.parse_ing_text(ing_text(rows), ING, ref())
    assert [t.type for t in txs] == ["ppk_state", "buy"]


def _rebalance(units):
    return ("05.10.2025", "Complete", "Rebalance", "PPK Rebalance", "PPK", "x", ING_FUND, "", units, "A")


def _buy(day, amount, units):
    return (day, "Complete", "Purchase", "PPK Primary employee's payment", "PPK", "x", ING_FUND, amount, units, "A")


def test_ing_rebalance_of_all_units_is_skipped():
    rows = [_buy("13.10.2025", "100,00", "4,000000"), _rebalance("1 500,500000"),
            _buy("13.09.2025", "100,00", "500,500000"), _buy("13.08.2025", "1 000,00", "1 000,000000")]
    txs, notes = ppk.parse_ing_text(ing_text(rows), ING, ref())
    assert len(txs) == 6 and sum(t.amount for t in txs) == 0
    assert notes == [(f"rebalance on 2025-10-05: 1500.500000 units of {ING_FUND!r} = all units held, "
                      "no change, not imported")]


def test_ing_rebalance_with_other_units_stops_the_file():
    rows = [_rebalance("10,000000"), _buy("13.09.2025", "100,00", "4,000000")]
    with pytest.raises(ParseError, match="add up to 4.000000 units"):
        ppk.parse_ing_text(ing_text(rows), ING, ref())
