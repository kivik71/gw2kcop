"""Manual opening balances; synthetic data only."""

import csv
import io

from gw2kcop.cli import main

HEADER = "account_id,date,instrument,quantity,price,price_currency,amount,note\n"


def setup(tmp_path, rows):
    data = tmp_path / "Dane"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{(tmp_path / "E").as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    data.mkdir()
    (data / "accounts.csv").write_text(
        "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
        "OWNA-TEST,erste_brokerage,owna,,Test,brokerage,PLN,TRUE,parser\n", encoding="utf-8")
    (data / "instruments.csv").write_text("ticker,name,isin\nWSE:TEST,Test,PLTEST000016\n", encoding="utf-8")
    (data / "opening_balances.csv").write_text(HEADER + rows, encoding="utf-8")
    return cfg, data


def test_opening_balances(tmp_path, capsys):
    cfg, data = setup(tmp_path, "OWNA-TEST,2016-12-31,WSE:TEST,10,25.40,PLN,,avg cost from holdings report\n"
                                "OWNA-TEST,2016-12-31,,,,,1200.00,\n")
    assert main(["--config", str(cfg), "transactions", "opening", "--dry-run"]) == 0
    assert "would add 2, already known 0" in capsys.readouterr().out
    assert not (data / "transactions.csv").exists()
    assert main(["--config", str(cfg), "transactions", "opening"]) == 0
    assert "added 2, already known 0" in capsys.readouterr().out
    rows = list(csv.DictReader(io.StringIO((data / "transactions.csv").read_text(encoding="utf-8"))))
    pos, cash = rows
    assert pos["type"] == cash["type"] == "opening_balance"
    assert (pos["key"], pos["quantity"], pos["price"], pos["amount"], pos["isin"]) == (
        "erste_brokerage|OWNA-TEST|open:WSE:TEST:2016-12-31", "10", "25.4", "0", "PLTEST000016")
    assert (cash["key"], cash["amount"], cash["currency"], cash["description"]) == (
        "erste_brokerage|OWNA-TEST|open:cash:2016-12-31", "1200", "PLN", "opening balance")
    assert main(["--config", str(cfg), "transactions", "opening"]) == 0
    assert "added 0, already known 2" in capsys.readouterr().out


def test_every_problem_is_listed(tmp_path, capsys):
    cfg, data = setup(tmp_path, "OWNA-NONE,2016-12-31,WSE:TEST,10,25.40,PLN,,\n"
                                "OWNA-TEST,31.12.2016,WSE:TEST,10,25.40,PLN,,\n"
                                "OWNA-TEST,2016-12-31,WSE:OTHER,0,25,40,PLN,,\n"
                                "OWNA-TEST,2016-12-31,WSE:TEST,10,\"25,40\",PLN,5,\n"
                                "OWNA-TEST,2016-12-31,,1,,,0,\n")
    assert main(["--config", str(cfg), "transactions", "opening"]) != 0
    out = capsys.readouterr().out
    for message in ("not in accounts.csv", "YYYY-MM-DD", "too many values", "not a number like 12.50",
                    "a position row has no amount", "a cash row has only amount", "amount 0"):
        assert message in out, message
    assert not (data / "transactions.csv").exists()


def test_bad_header_and_missing_file(tmp_path, capsys):
    cfg, data = setup(tmp_path, "")
    (data / "opening_balances.csv").write_text("account,date\n", encoding="utf-8")
    assert main(["--config", str(cfg), "transactions", "opening"]) != 0
    assert "header must be" in capsys.readouterr().out
    (data / "opening_balances.csv").unlink()
    assert main(["--config", str(cfg), "transactions", "opening"]) != 0
    assert "not found" in capsys.readouterr().err


def test_duplicate_row_is_an_error(tmp_path, capsys):
    row = "OWNA-TEST,2016-12-31,WSE:TEST,10,25.40,PLN,,\n"
    cfg, data = setup(tmp_path, row + row)
    assert main(["--config", str(cfg), "transactions", "opening"]) != 0
    assert "duplicate" in capsys.readouterr().err


def test_schema_rules():
    from datetime import date
    from decimal import Decimal

    import pytest

    from gw2kcop.transactions.master import Transaction, TransactionsError, validate

    base = dict(date=date(2016, 12, 31), account_id="OWNA-TEST", institution="test", type="opening_balance",
                currency="PLN")
    validate(Transaction(**base, instrument="WSE:TEST", quantity=Decimal(1), price=Decimal(1), amount=Decimal(0)))
    validate(Transaction(**base, amount=Decimal(5)))
    for bad in (dict(instrument="WSE:TEST", quantity=Decimal(1), price=Decimal(1), amount=Decimal(1)),
                dict(instrument="WSE:TEST", price=Decimal(1), amount=Decimal(0)),
                dict(amount=Decimal(0)),
                dict(amount=Decimal(5), quantity=Decimal(1))):
        with pytest.raises(TransactionsError):
            validate(Transaction(**base, **bad))
