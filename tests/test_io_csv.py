import pytest

from gw2kcop.io.csvio import DelimiterError, read_csv_rows, sniff_delimiter

# Synthetic, shaped like the real exports.
SEMICOLON = (
    "Data operacji;Typ operacji;Kwota;Waluta Kwota;Opis\n"
    "15-05-2026;Uznanie;2,50;PLN;NOT: 1 ZLC: 2 PW: PLTEST000000\n"
    "15-05-2026;Obciążenie;-5,00;PLN;NOT: 1 ZLC: 2 PW: PLTEST000000\n"
)
QUOTED_COMMA = (
    '"Transaction history"\n'
    '"File created","3.11.2026, 18:39"\n'
    "\n"
    '"Date","Transaction type","Amount","Number of units"\n'
    '"13.10.2026","Purchase","1 000,00","22,500000"\n'
    '"13.09.2026","Purchase","500,00","11,250000"\n'
)


def test_semicolon():
    assert sniff_delimiter(SEMICOLON) == ";"


def test_quoted_comma_with_metadata_rows():
    assert sniff_delimiter(QUOTED_COMMA) == ","


def test_tab():
    assert sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"


def test_no_delimiter():
    with pytest.raises(DelimiterError):
        sniff_delimiter("one column\nonly\n")


def test_read_rows_drops_empty_lines(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text(QUOTED_COMMA, encoding="utf-8-sig")
    rows, delimiter, encoding = read_csv_rows(path)
    assert delimiter == ","
    assert encoding == "utf-8-sig"
    assert rows[0] == ["Transaction history"]
    assert rows[3] == ["13.10.2026", "Purchase", "1 000,00", "22,500000"]
    assert len(rows) == 5
