"""Master transactions.csv: keys, dedup, formatting, safe writes (synthetic data only)."""

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from gw2kcop.transactions.master import (
    COLUMNS,
    Transaction,
    TransactionsError,
    assign_keys,
    format_decimal,
    merge,
    neutralize,
    read_master,
)

NOW = datetime(2026, 11, 10, 18, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 11, 9, 0, 0, tzinfo=timezone.utc)


def tx(**kw):
    base = dict(date=date(2026, 5, 22), account_id="OWNA-TEST-BM", institution="test_broker",
                type="dividend", amount=Decimal("12.50"), currency="PLN",
                description="Dywidenda: DP: 2026-05-15 PLTEST000016 (TEST);")
    base.update(kw)
    return Transaction(**base)


def rows(path):
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def test_key_with_source_id():
    [(key, _)] = assign_keys([tx(source_id="38427129")])
    assert key == "test_broker|OWNA-TEST-BM|38427129"


def test_hash_keys_count_identical_rows():
    keys = [k for k, _ in assign_keys([tx(), tx(), tx(amount=Decimal("1"))])]
    assert keys[0].endswith("|1") and keys[1].endswith("|2")
    assert keys[0].rsplit("|", 1)[0] == keys[1].rsplit("|", 1)[0]
    assert keys[2].endswith("|1") and keys[2] != keys[0]
    assert keys[0].split("|")[2].startswith("h:") and len(keys[0].split("|")[2]) == 18


def test_hash_ignores_trailing_zeros():
    a = assign_keys([tx(amount=Decimal("12.50"))])[0][0]
    b = assign_keys([tx(amount=Decimal("12.5"))])[0][0]
    assert a == b


def test_duplicate_source_id_in_one_file_fails():
    with pytest.raises(TransactionsError, match="duplicate source_id"):
        assign_keys([tx(source_id="1"), tx(source_id="1")])


@pytest.mark.parametrize("value, text", [
    (Decimal("-2.50"), "-2.5"), (Decimal("5.00"), "5"), (Decimal("1E+3"), "1000"),
    (Decimal("0.000001"), "0.000001"), (Decimal("-0.00"), "0"), (None, ""),
])
def test_format_decimal(value, text):
    assert format_decimal(value) == text


@pytest.mark.parametrize("text, out", [
    ("=HYPERLINK(1)", "'=HYPERLINK(1)"), ("+48", "'+48"), ("-x", "'-x"), ("@a", "'@a"),
    ("\tx", "'\tx"), ("plain", "plain"), ("", ""),
])
def test_neutralize(text, out):
    assert neutralize(text) == out


def test_merge_creates_file_and_dedups(tmp_path):
    batch = [tx(source_id="1"), tx(source_id="2", type="tax", amount=Decimal("-2.38"))]
    first = merge(tmp_path, batch, "Historia_operacji_test.csv", now=NOW)
    assert (first.added, first.skipped) == (2, 0)
    master = tmp_path / "transactions.csv"
    assert master.read_text(encoding="utf-8").splitlines()[0] == ",".join(COLUMNS)
    again = merge(tmp_path, batch + [tx(source_id="3")], "Historia_operacji_test2.csv", now=LATER)
    assert (again.added, again.skipped) == (1, 2)
    data = rows(master)
    assert [r["source_id"] for r in data] == ["1", "2", "3"]
    assert data[0]["imported_at"] == "2026-11-10T18:00:00Z"   # old rows untouched
    assert data[2]["imported_at"] == "2026-11-11T09:00:00Z"
    log = rows(tmp_path / "import_log.csv")
    assert [(r["added"], r["skipped"]) for r in log] == [("2", "0"), ("1", "2")]
    backups = list((tmp_path / ".backup").glob("transactions-*.csv"))
    assert len(backups) == 1 and len(rows(backups[0])) == 2


def test_reimport_same_file_adds_nothing_and_keeps_file(tmp_path):
    batch = [tx(), tx()]
    merge(tmp_path, batch, "a.csv", now=NOW)
    before = (tmp_path / "transactions.csv").read_bytes()
    result = merge(tmp_path, batch, "a.csv", now=LATER)
    assert (result.added, result.skipped) == (0, 2)
    assert (tmp_path / "transactions.csv").read_bytes() == before
    assert not (tmp_path / ".backup").exists()


def test_trade_row_fields_and_formula_neutralized(tmp_path):
    trade = tx(type="sell", instrument="WSE:TEST", isin="PLTEST000016", quantity=Decimal("-4"),
               price=Decimal("0.625"), price_currency="PLN", amount=Decimal("-2.50"),
               fee=Decimal("5.00"), description="=1+1", source_id="26423031",
               group_id="test_broker|26423031",
               timestamp_utc=datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc))
    merge(tmp_path, [trade], "Raport_transakcji_test.csv", now=NOW)
    [row] = rows(tmp_path / "transactions.csv")
    assert row["quantity"] == "-4" and row["amount"] == "-2.5" and row["fee"] == "5"
    assert row["description"] == "'=1+1"
    assert row["timestamp_utc"] == "2026-05-22T09:00:00Z"


@pytest.mark.parametrize("bad", [
    dict(type="buy_stuff"),
    dict(account_id="owna-test"),
    dict(currency="pln"),
    dict(fee=Decimal("-1")),
    dict(isin="PL123"),
    dict(type="buy", instrument="WSE:TEST", quantity=Decimal("-1")),
    dict(type="sell", instrument="WSE:TEST", quantity=Decimal("1")),
    dict(type="buy", quantity=Decimal("1")),
    dict(amount=Decimal("NaN")),
    dict(source_id="a|b"),
    dict(timestamp_utc=datetime(2026, 5, 22, 9, 0, 0)),
])
def test_invalid_row_writes_nothing(tmp_path, bad):
    with pytest.raises(TransactionsError):
        merge(tmp_path, [tx(source_id="1"), tx(**{"source_id": "2", **bad})], "x.csv", now=NOW)
    assert not (tmp_path / "transactions.csv").exists()
    assert not (tmp_path / "import_log.csv").exists()


def test_gbp_pence_currency_allowed(tmp_path):
    merge(tmp_path, [tx(type="buy", instrument="LON:TEST", quantity=Decimal("1"),
                        price=Decimal("1234.5"), price_currency="GBp", amount=Decimal("-60"),
                        currency="EUR")], "x.csv", now=NOW)
    assert rows(tmp_path / "transactions.csv")[0]["price_currency"] == "GBp"


def test_wrong_header_fails_closed(tmp_path):
    (tmp_path / "transactions.csv").write_text("key,date\nx,2026-01-01\n", encoding="utf-8")
    with pytest.raises(TransactionsError, match="header"):
        merge(tmp_path, [tx()], "x.csv", now=NOW)


def test_duplicate_keys_in_master_fail_closed(tmp_path):
    merge(tmp_path, [tx(source_id="1")], "x.csv", now=NOW)
    path = tmp_path / "transactions.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([*lines, lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(TransactionsError, match="duplicate keys"):
        read_master(path)


def test_backups_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr("gw2kcop.transactions.master.BACKUPS_KEPT", 3)
    for n in range(6):
        merge(tmp_path, [tx(source_id=str(n))], "x.csv",
              now=datetime(2026, 11, 10, 18, 0, n, tzinfo=timezone.utc))
    assert len(list((tmp_path / ".backup").iterdir())) == 3
    assert len(rows(tmp_path / "transactions.csv")) == 6
