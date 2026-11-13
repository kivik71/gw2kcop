"""instruments.csv / instrument_aliases.csv loading (synthetic data only)."""

import pytest

from gw2kcop.transactions.reference import ReferenceError, load_reference


def test_extra_values_give_line_number(tmp_path):
    (tmp_path / "instruments.csv").write_text(
        "ticker,name,isin\nWSE:TEST,Test,PLTEST000016\nETR:TEST,Test, Inc,DETEST000012\n", encoding="utf-8")
    with pytest.raises(ReferenceError, match=r"instruments.csv:3: 4 values but 3 columns.*double quotes"):
        load_reference(tmp_path)


def test_quoted_comma_is_fine(tmp_path):
    (tmp_path / "instruments.csv").write_text(
        'ticker,name,isin\nETR:TEST,"Test, Inc",DETEST000012\n', encoding="utf-8")
    assert load_reference(tmp_path).tickers["ETR:TEST"]["name"] == "Test, Inc"
