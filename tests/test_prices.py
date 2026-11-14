"""Prices: synthetic Yahoo answers through a fake yfinance module, synthetic BAMOSZ file. No network."""

import csv
import io
import math
import sys
import types
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from gw2kcop.cli import main
from gw2kcop.prices import bamosz, store, yahoo
from gw2kcop.transactions.master import COLUMNS


class FakeFrame:
    def __init__(self, rows):
        self.index = [datetime.fromisoformat(d).replace(tzinfo=timezone.utc) for d, _ in rows]
        self.close = [c for _, c in rows]

    def __len__(self):
        return len(self.index)

    def __getitem__(self, name):
        assert name == "Close"
        return self.close


def fake_yf(data, currency=None, calls=None):
    """data: symbol -> [(day, close)]; currency: symbol -> code (default EUR)."""
    calls = [] if calls is None else calls

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.history_metadata = {}

        def history(self, start, end, **kwargs):
            assert kwargs["auto_adjust"] is False
            calls.append((self.symbol, start, end))
            if self.symbol == "BROKEN":
                raise RuntimeError("no data")
            self.history_metadata = {"currency": (currency or {}).get(self.symbol, "EUR")}
            return FakeFrame([(d, c) for d, c in data.get(self.symbol, []) if start <= d < end])

    return types.SimpleNamespace(Ticker=Ticker, calls=calls)


def test_yahoo_fetch_converts_pence_and_skips_gaps():
    yf = fake_yf({"AIAG.L": [("2026-01-02", 1234.5), ("2026-01-05", math.nan)]}, {"AIAG.L": "GBp"})
    currency, rows = yahoo.fetch("AIAG.L", date(2026, 1, 1), date(2026, 1, 31), yf=yf)
    assert currency == "GBP" and rows == [(date(2026, 1, 2), Decimal("12.345000"))]


def test_yahoo_fetch_reports_errors():
    with pytest.raises(store.PriceError, match="Yahoo BROKEN: RuntimeError"):
        yahoo.fetch("BROKEN", date(2026, 1, 1), date(2026, 1, 2), yf=fake_yf({}))
    yf = fake_yf({"X": [("2026-01-02", 5.0)]}, {"X": "ZAc"})
    with pytest.raises(store.PriceError, match="unexpected currency"):
        yahoo.fetch("X", date(2026, 1, 1), date(2026, 1, 2), yf=yf)


def test_yahoo_needs_the_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)
    with pytest.raises(store.PriceError, match="pip install yfinance"):
        yahoo.fetch("X", date(2026, 1, 1), date(2026, 1, 2))


def test_merge_keeps_known_and_refuses_changed(tmp_path):
    path = tmp_path / "prices.csv"
    p = store.Price(date(2026, 1, 2), "WSE:TEST", Decimal("25.4"), "PLN", "yahoo")
    assert store.merge(path, [p]) == (1, 0)
    assert store.merge(path, [p]) == (0, 1)
    with pytest.raises(store.PriceError, match="nothing written"):
        store.merge(path, [store.Price(p.date, p.ticker, Decimal("25.5"), "PLN", "yahoo")])
    assert path.read_text(encoding="utf-8") == "date,ticker,price,currency,source\n2026-01-02,WSE:TEST,25.4,PLN,yahoo\n"


# SYNTHETIC: layout of a BAMOSZ export, invented fund and numbers.
BAMOSZ = (
    '"Letöltés időpontja","2026.01.10. 12:00"\n'
    '"Alap neve:","SYNTHETIC Test Fund"\n'
    '"Befektetési sorozat megjelölése:","EUR-X"\n'
    '"Befektetési jegy devizaneme:","EUR"\n'
    '"Dátum","Árfolyam","NEÉ","Kifizetett hozam"\n'
    '"2026/01/08","10,123456","1000,5","0"\n'
    '"2026/01/07","","","0"\n'
    '"2026/01/06","10,1","999","0"\n'
)


def test_bamosz_history(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes(BAMOSZ.encode("cp1250"))
    h = bamosz.read_history(path)
    assert (h.name, h.series, h.currency) == ("SYNTHETIC Test Fund", "EUR-X", "EUR")
    assert h.rows == [(date(2026, 1, 6), Decimal("10.1")), (date(2026, 1, 8), Decimal("10.123456"))]


@pytest.mark.parametrize("change, message", [
    (("Dátum", "Datum"), "no table"),
    (('devizaneme:","EUR"', 'devizaneme:",""'), "currency line"),
    (("2026/01/08", "08.01.2026"), "not RRRR/MM/DD"),
    (("10,123456", "10.123456"), "not a number"),
])
def test_bamosz_fails_closed(tmp_path, change, message):
    path = tmp_path / "data.csv"
    path.write_bytes(BAMOSZ.replace(*change).encode("cp1250"))
    with pytest.raises(store.PriceError, match=message):
        bamosz.read_history(path)


def setup_data(tmp_path):
    data = tmp_path / "Dane"
    data.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{(tmp_path / "E").as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    (data / "instruments.csv").write_text(
        "ticker,name,isin,market,currency,asset_class,price_source,source_symbol\n"
        "WSE:TEST,SYNTHETIC,,WSE,PLN,,yahoo,TEST.WA\n"
        "WSE:OTHER,SYNTHETIC,,WSE,,,yahoo,BROKEN\n"
        "FUND:TEST,SYNTHETIC,,,EUR,,manual,\n", encoding="utf-8")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for i, inst in enumerate(("WSE:TEST", "WSE:OTHER", "FUND:TEST"), 1):
        row = dict.fromkeys(COLUMNS, "")
        row.update(key=f"t|A-B|{i}", date="2026-01-10", account_id="A-B", institution="t", type="buy",
                   instrument=inst, quantity="1", price="1", price_currency="PLN", amount="-1",
                   currency="PLN", source_file="x", imported_at="2026-01-10T00:00:00Z")
        w.writerow(row)
    (data / "transactions.csv").write_text(buf.getvalue(), encoding="utf-8")
    return data, ["--config", str(cfg)]


def test_prices_download_command(tmp_path, capsys, monkeypatch):
    data, cfg = setup_data(tmp_path)
    yf = fake_yf({"TEST.WA": [("2026-01-02", 25.4), ("2026-01-09", 26.0)]}, {"TEST.WA": "PLN"})
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    args = [*cfg, "prices", "download", "--to", "2026-01-31"]
    assert main([*args, "--dry-run"]) == 1  # WSE:OTHER fails, the rest is still checked
    out = capsys.readouterr().out
    assert "! WSE:OTHER: Yahoo BROKEN" in out and "would add 1" in out
    assert not (data / "prices.csv").exists()
    assert ("TEST.WA", "2026-01-03", "2026-02-01") in yf.calls  # a week before the first transaction
    assert all(c[0] != "" for c in yf.calls)  # the manual fund is never downloaded
    assert main([*args, "--tickers", "WSE:TEST"]) == 0
    assert "added 1, already known 0" in capsys.readouterr().out
    yf.calls.clear()
    assert main([*args, "--tickers", "WSE:TEST"]) == 0  # next run starts after the last known price
    assert yf.calls == [("TEST.WA", "2026-01-10", "2026-02-01")]
    assert main([*args, "--tickers", "FUND:TEST"]) == 1
    assert "needs price_source=yahoo" in capsys.readouterr().err


def test_prices_import_command(tmp_path, capsys):
    data, cfg = setup_data(tmp_path)
    path = tmp_path / "data.csv"
    path.write_bytes(BAMOSZ.encode("cp1250"))
    assert main([*cfg, "prices", "import", str(path), "--ticker", "FUND:TEST"]) == 0
    assert "added 2, already known 0 (FUND:TEST)" in capsys.readouterr().out
    assert (data / "prices.csv").read_text(encoding="utf-8").splitlines()[1:] == [
        "2026-01-06,FUND:TEST,10.1,EUR,bamosz", "2026-01-08,FUND:TEST,10.123456,EUR,bamosz"]
    assert main([*cfg, "prices", "import", str(path), "--ticker", "WSE:TEST"]) == 1  # PLN instrument
    assert "instruments.csv says WSE:TEST is in PLN" in capsys.readouterr().err
