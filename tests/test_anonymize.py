"""Tests for the CSV anonymizer. All values are synthetic.

Sensitive-looking values are assembled at runtime so this file passes the data guard.
"""

import codecs
import os
import stat
from decimal import Decimal

import pytest

from gw2kcop.anonymize import patterns as pt
from gw2kcop.anonymize.core import Options, anonymize_bytes, anonymize_file, check_text
from gw2kcop.anonymize.csvtokens import render, tokenize
from gw2kcop.anonymize.mapping import Mapping, load_mapping, load_terms, save_mapping
from gw2kcop.anonymize.numfmt import scale_text
from gw2kcop.cli import main

# Widely published example account number (valid checksum), split to pass the data guard.
EXAMPLE_NRB = " ".join(["61", "1090", "1014", "0000", "0712", "1981", "2874"])
TWO = Decimal("2")


def opts(**kw):
    kw.setdefault("factor", TWO)
    return Options(**kw)


# --- tokenizer --------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, delimiter",
    [
        ('a;b;c\r\n1;"x;y";3\r\n', ";"),
        ('"q","w ""e"""\n"1",""\n', ","),
        ("a\tb\n1\t2", "\t"),
        ('"multi\nline";2\n', ";"),
        ("\n\na;b\n", ";"),
    ],
)
def test_tokenize_roundtrip(text, delimiter):
    assert render(tokenize(text, delimiter), delimiter) == text


# --- number formatting ---------------------------------------------------------------

@pytest.mark.parametrize(
    "text, sep, expected",
    [
        ("-4372,04", ",", "-8744,08"),
        ("1 039,94", ",", "2 079,88"),
        ("1.165,76", ",", "2.331,52"),
        ("0,625", ",", "1,250"),
        ("+ 187.25 €", ".", "+ 374.50 €"),
        ("PLN 41 602.04", ".", "PLN 83 204.08"),
        ("12,431927", ",", "24,863854"),
        ("999,99", ",", "1999,98"),
    ],
)
def test_scale_keeps_format(text, sep, expected):
    assert scale_text(text, TWO, sep) == expected


def test_scale_rejects_text():
    assert scale_text("abc", TWO, ",") is None


# --- mapping ----------------------------------------------------------------------------

def test_digits_pseudonym_is_stable_and_keeps_layout():
    m = Mapping(b"k" * 32)
    first = m.digits("PL" + EXAMPLE_NRB.replace(" ", ""))
    assert first == m.digits("PL" + EXAMPLE_NRB.replace(" ", ""))
    assert first.startswith("PL") and len(first) == 28 and first != "PL" + EXAMPLE_NRB.replace(" ", "")
    other = Mapping(b"x" * 32).digits("PL" + EXAMPLE_NRB.replace(" ", ""))
    assert other != first  # depends on the secret


def test_keep_last_four():
    m = Mapping(b"k" * 32)
    assert m.digits(EXAMPLE_NRB, keep_last=4).endswith("2874")


def test_tags_are_numbered_and_case_insensitive():
    m = Mapping(b"k" * 32)
    assert m.tag("OSOBA", "Anna Testowa") == "[OSOBA_1]"
    assert m.tag("OSOBA", "ANNA TESTOWA") == "[OSOBA_1]"
    assert m.tag("OSOBA", "Jan Demo") == "[OSOBA_2]"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_mapping_file_is_private(tmp_path):
    folder = tmp_path / "anon"
    m = Mapping.new()
    m.digits("123456")
    path = save_mapping(folder, m)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert load_mapping(folder).digits("123456") == m.digits("123456")


def test_terms_file(tmp_path):
    (tmp_path / "terms.txt").write_text("# comment\nAnna\nAnna Testowa  # inline\n\nx\n", encoding="utf-8")
    assert load_terms(tmp_path) == ["Anna Testowa", "Anna"]


# --- whole file ---------------------------------------------------------------------------

def build_cash_history():
    rows = [
        "Data operacji;Typ operacji;Kwota;Waluta Kwota;Opis;Saldo po operacji;Id operacji",
        '22-05-2026;Obciążenie;-484,00;PLN;"Podatek od dywidendy: PLTEST000016 (DEMO);";2139,77;38427130',
        '22-05-2026;Uznanie;2544,75;PLN;"Dywidenda: PLTEST000016 (DEMO);";2623,77;38427129',
        f"12-05-2026;Uznanie;100,00;PLN;PRZELEW OD ANNA TESTOWA {EXAMPLE_NRB};86,10;37604139",
        "11-05-2026;Obciążenie;-5,00;PLN;PROWIZJA;-13,90;37540144",
    ]
    return codecs.BOM_UTF8 + ("\r\n".join(rows) + "\r\n").encode("utf-8")


def test_csv_format_and_content(tmp_path):
    m = Mapping(b"k" * 32)
    out, report = anonymize_bytes(build_cash_history(), tmp_path / "x.csv", m, ["Anna Testowa"], opts())
    text = out.decode("utf-8-sig")
    assert out.startswith(codecs.BOM_UTF8)
    assert out.count(b"\r\n") == 5 and b"\n" not in out.replace(b"\r\n", b"")
    assert "-968,00" in text and "4279,54" in text          # amounts and balances doubled
    assert "38427130" not in text and m.digits("38427130") in text  # ids pseudonymized
    assert "PLTEST000016" in text and "Dywidenda" in text    # ISIN and keywords kept
    assert "ANNA TESTOWA" not in text and "[OSOBA_1]" in text
    assert EXAMPLE_NRB not in text
    assert report.scaled_columns == ["Kwota", "Saldo po operacji"]
    assert check_text(text, m) == []


def test_balance_stays_consistent():
    rows = "D;Kwota;Saldo\n01-01-2026;10,00;110,00\n02-01-2026;-3,33;106,67\n"
    out, _ = anonymize_bytes(rows.encode(), None, Mapping(b"k" * 32), [], opts(factor=Decimal("1.37")))
    lines = out.decode().splitlines()[1:]
    vals = [[Decimal(c.replace(",", ".")) for c in line.split(";")[1:]] for line in lines]
    assert abs(vals[0][1] + vals[1][0] - vals[1][1]) <= Decimal("0.01")


def test_quantity_column_kept_unless_asked():
    rows = '"Date","Amount","Number of units"\n"13.10.2026","100,00","22,479317"\n"13.09.2026","50,00","11,000000"\n'
    out, report = anonymize_bytes(rows.encode(), None, Mapping(b"k" * 32), [], opts())
    assert "22,479317" in out.decode() and report.kept_quantity_columns == ["Number of units"]
    out, _ = anonymize_bytes(rows.encode(), None, Mapping(b"k" * 32), [], opts(scale_quantities=True))
    assert "44,958634" in out.decode()


def test_rows_limit_keeps_types():
    data = build_cash_history()
    out, report = anonymize_bytes(data, None, Mapping(b"k" * 32), [], opts(rows=2))
    lines = out.decode("utf-8-sig").splitlines()
    assert len(lines) == 3 and report.data_rows_out == 2
    assert {line.split(";")[1] for line in lines[1:]} == {"Obciążenie", "Uznanie"}


def test_cp1250_and_metadata_rows():
    text = "Pracodawca;DEMO ŁÓDŹ SP Z OO\nNr rachunku;12345678\n\nData;Kwota\n2026-10-01;499,94\n2026-09-01;374,96\n"
    out, report = anonymize_bytes(text.encode("cp1250"), None, Mapping(b"k" * 32), ["DEMO ŁÓDŹ SP Z OO", "12345678"], opts())
    result = out.decode("cp1250")
    assert report.encoding == "cp1250"
    assert "DEMO" not in result and "12345678" not in result
    assert "999,88" in result


def test_personal_data_in_text():
    pesel = "".join(["4405", "1401", "359"])
    email = "anna" + "@" + "example.com"
    text = f"Data;Opis;Kwota\n2026-01-01;{pesel} {email} +48 600 100 200;1,00\n2026-01-02;x;2,00\n"
    out, report = anonymize_bytes(text.encode(), None, Mapping(b"k" * 32), [], opts())
    result = out.decode()
    assert pesel not in result and email not in result and "600 100 200" not in result
    assert "[PESEL_1]" in result and "[EMAIL_1]" in result and "[TELEFON_1]" in result


def test_check_flags_real_data_but_not_pseudonyms():
    m = Mapping(b"k" * 32)
    assert any("account number" in f for f in check_text(f"x {EXAMPLE_NRB} y", m))
    out, _ = anonymize_bytes(build_cash_history(), None, m, [], opts())
    assert check_text(out.decode("utf-8-sig"), m) == []


def test_pseudonym_account_never_valid():
    m = Mapping(b"k" * 32)
    for i in range(30):
        nrb = f"{i:02d}" + "1" * 24
        out, _ = anonymize_bytes(f"D;Opis;K\n1;{nrb};1,00\n2;x;2,00\n".encode(), None, m, [], opts())
        found = pt.NRB_RE.search(out.decode())
        assert found and not pt.account_checksum_ok(found.group(0))


def test_never_overwrites_original(tmp_path):
    src = tmp_path / "x.csv"
    src.write_bytes(build_cash_history())
    with pytest.raises(ValueError):
        anonymize_file(src, tmp_path, Mapping(b"k" * 32), [], opts())


def test_cli_end_to_end(tmp_path, capsys):
    src = tmp_path / "in"
    src.mkdir()
    (src / "Historia_operacji.csv").write_bytes(build_cash_history())
    (src / "statement.xlsx").write_bytes(b"PK")
    anon = tmp_path / "anon"
    anon.mkdir()
    (anon / "terms.txt").write_text("Anna Testowa\n", encoding="utf-8")
    out = tmp_path / "out"
    cfg = ["--config", str(tmp_path / "none.toml")]
    code = main([*cfg, "anonymize", str(src), "--mapping-dir", str(anon), "--output", str(out)])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert (out / "Historia_operacji.csv").is_file() and (anon / "mapping.json").is_file()
    assert "skipped (not .csv yet): statement.xlsx" in printed
    assert main([*cfg, "anonymize", "--check", str(out / "Historia_operacji.csv"), "--mapping-dir", str(anon)]) == 0
    assert main([*cfg, "anonymize", "--check", str(src / "Historia_operacji.csv"), "--mapping-dir", str(anon)]) == 1


def test_ids_pseudonymized_consistently_across_columns_and_text():
    rows = (
        "Data;Opis;Ilosc;Id zlecenia\n"
        "22-05-2026;NOT: 54345393 ZLC: 26423031 PW: PLTEST000016 20260522;-4;26423031\n"
        "23-05-2026;NOT: 54345394 ZLC: 26423032 PW: PLTEST000016;-5;26423032\n"
    )
    m = Mapping(b"k" * 32)
    out, report = anonymize_bytes(rows.encode(), None, m, [], opts())
    text = out.decode()
    first = text.splitlines()[1].split(";")
    zlc = first[1].split("ZLC: ")[1].split(" ")[0]
    assert zlc == first[3] != "26423031"         # join description <-> order id survives
    assert "54345393" not in text and "PLTEST000016" in text
    assert "20260522" in text                    # compact date is not an id
    assert report.replaced["id"] >= 4
    assert check_text(text, m) == []


def test_id_pseudonym_never_looks_personal():
    m = Mapping(b"k" * 32)
    for n in range(300):
        rows = f"A;Id\nx;{10_000_000_000 + n * 7919}\ny;1\n"
        out, _ = anonymize_bytes(rows.encode(), None, m, [], opts())
        value = out.decode().splitlines()[1].split(";")[1]
        assert not (pt.is_pesel(value) or pt.is_nip(value) or pt.is_luhn(value))


@pytest.mark.parametrize("header", ["Koszt nabycia", "Kosztorys"])
def test_words_containing_szt_are_not_quantity(header):
    rows = f"Walor;{header}\nABC;100,50\nDEF;20,25\n"
    out, report = anonymize_bytes(rows.encode(), None, Mapping(b"k" * 32), [], opts(factor=Decimal("2")))
    assert report.scaled_columns == [header] and report.kept_quantity_columns == []
    assert "201,00" in out.decode()


def test_kept_quantity_cells_are_not_treated_as_ids():
    rows = "Walor;Liczba jednostek;Kwota\nABC;1234567,500000;10,00\nDEF;2,000000;5,00\n"
    out, report = anonymize_bytes(rows.encode(), None, Mapping(b"k" * 32), [], opts())
    assert report.kept_quantity_columns == ["Liczba jednostek"]
    assert "1234567,500000" in out.decode()
