"""NBP rates: recorded synthetic answers, no network."""

import csv
import io
import json
from datetime import date
from decimal import Decimal

import pytest

from gw2kcop.cli import main
from gw2kcop.fx import nbp
from gw2kcop.fx import rates as fx


def answer(code, rates):
    return json.dumps({"table": "A", "currency": "x", "code": code, "rates": [
        {"no": f"{i:03d}/A/NBP/{day[:4]}", "effectiveDate": day, "mid": mid} for i, (day, mid) in enumerate(rates, 1)
    ]}).encode()


class FakeNbp:
    def __init__(self, data):
        self.data = data  # code -> [(day, mid)]
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        code, start, end = url.split("/rates/A/")[1].split("/")[:3]
        rows = [(d, m) for d, m in self.data.get(code.upper(), []) if start <= d <= end]
        return answer(code.upper(), rows) if rows else None


def test_download_validates_and_splits_long_ranges():
    fake = FakeNbp({"USD": [("2025-01-02", 4.1), ("2026-01-05", 3.9)]})
    rates = nbp.download("USD", date(2025, 1, 1), date(2026, 1, 31), fetch=fake)
    assert [(r.date.isoformat(), r.rate) for r in rates] == [
        ("2025-01-02", Decimal("4.1")), ("2026-01-05", Decimal("3.9"))]
    assert len(fake.urls) == 2  # 396 days -> two requests of at most 367 days
    assert all(u.startswith("https://api.nbp.pl/api/exchangerates/rates/A/usd/") for u in fake.urls)


@pytest.mark.parametrize("body, message", [
    (json.dumps({"table": "B", "code": "USD", "rates": []}).encode(), "answer for B/USD"),
    (json.dumps({"table": "A", "code": "EUR", "rates": []}).encode(), "answer for A/EUR"),
    (json.dumps({"table": "A", "code": "USD", "rates": [
        {"no": "001/A/NBP/2026", "effectiveDate": "2026-01-02", "mid": 0}]}).encode(), "implausible"),
    (json.dumps({"table": "A", "code": "USD", "rates": [
        {"no": "001/A/NBP/2026", "effectiveDate": "2020-01-02", "mid": 4}]}).encode(), "implausible"),
    (b"<html>", "unexpected answer"),
])
def test_download_fails_closed(body, message):
    with pytest.raises(fx.FxError, match=message):
        nbp.download("USD", date(2026, 1, 1), date(2026, 1, 31), fetch=lambda url: body)


def test_only_nbp_host_and_codes():
    with pytest.raises(fx.FxError, match="only api.nbp.pl"):
        nbp.http_fetch("https://example.com/x")
    with pytest.raises(fx.FxError, match="foreign currency"):
        nbp.download("PLN", date(2026, 1, 1), date(2026, 1, 2), fetch=lambda url: None)


def test_rates_are_pln_based_nbp():
    fake = FakeNbp({"USD": [("2026-01-02", 4.1)]})
    (rate,) = nbp.download("USD", date(2026, 1, 1), date(2026, 1, 31), fetch=fake)
    assert (rate.base_currency, rate.currency, rate.source, rate.table) == ("PLN", "USD", "NBP", "001/A/NBP/2026")


def test_merge_keeps_known_and_refuses_changed(tmp_path):
    path = tmp_path / "fx_rates.csv"
    r1 = fx.Rate(date(2026, 1, 2), "PLN", "USD", Decimal("4.1"), "NBP", "001/A/NBP/2026")
    assert fx.merge(path, [r1]) == (1, 0)
    assert fx.merge(path, [r1]) == (0, 1)
    with pytest.raises(fx.FxError, match="nothing written"):
        fx.merge(path, [fx.Rate(r1.date, "PLN", "USD", Decimal("4.2"), "NBP", r1.table)])
    assert path.read_text(encoding="utf-8") == (
        "date,base_currency,currency,rate,source,table\n2026-01-02,PLN,USD,4.1,NBP,001/A/NBP/2026\n")


def test_other_base_currencies_live_side_by_side(tmp_path):
    path = tmp_path / "fx_rates.csv"
    pln = fx.Rate(date(2026, 1, 2), "PLN", "USD", Decimal("4.1"), "NBP", "001/A/NBP/2026")
    sek = fx.Rate(date(2026, 1, 2), "SEK", "USD", Decimal("10.5"), "TEST")  # synthetic rate, no table id
    assert fx.merge(path, [pln, sek]) == (2, 0)
    assert set(fx.read_rates(path)) == {pln.key, sek.key}
    assert fx.last_known(path, "PLN") == {"USD": date(2026, 1, 2)}
    assert fx.last_known(path, "EUR") == {}


def test_bad_file_is_refused(tmp_path):
    path = tmp_path / "fx_rates.csv"
    path.write_text("date,currency,rate_pln,nbp_table\n", encoding="utf-8")
    with pytest.raises(fx.FxError, match="header must be"):
        fx.read_rates(path)
    path.write_text("date,base_currency,currency,rate,source,table\n2026-01-02,PLN,USD,0,NBP,\n", encoding="utf-8")
    with pytest.raises(fx.FxError, match="rate > 0"):
        fx.read_rates(path)


def test_fx_command(tmp_path, capsys, monkeypatch):
    data = tmp_path / "Dane"
    data.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{(tmp_path / "E").as_posix()}"\n'
                   f'data = "{data.as_posix()}"\naccounts = "{(data / "accounts.csv").as_posix()}"\n',
                   encoding="utf-8")
    from gw2kcop.transactions.master import COLUMNS
    row = dict.fromkeys(COLUMNS, "")
    row.update(key="t|A-B|1", date="2026-01-10", account_id="A-B", institution="t", type="deposit",
               amount="1", currency="USD", price_currency="EUR", source_file="x", imported_at="2026-01-10T00:00:00Z")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerow(row)
    (data / "transactions.csv").write_text(buf.getvalue(), encoding="utf-8")
    fake = FakeNbp({"USD": [("2026-01-02", 4.1), ("2026-01-09", 4.0)], "EUR": [("2026-01-09", 4.3)]})
    monkeypatch.setattr(nbp, "http_fetch", fake)
    args = ["--config", str(cfg), "fx", "--to", "2026-01-31"]
    assert main([*args, "--dry-run"]) == 0
    # a week before the first transaction (2026-01-10): from 2026-01-03, so the 2026-01-02 rate is not needed
    assert "would add 2" in capsys.readouterr().out and not (data / "fx_rates.csv").exists()
    assert all("/2026-01-03/2026-01-31/" in u for u in fake.urls)
    assert main(args) == 0
    assert "added 2, already known 0" in capsys.readouterr().out
    fake.urls.clear()
    assert main(args) == 0  # next run starts the day after the last known rate
    assert all("/2026-01-10/" in u for u in fake.urls)
    assert "added 0" in capsys.readouterr().out
