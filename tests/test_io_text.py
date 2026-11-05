import codecs

import pytest

from gw2kcop.io.text import UnknownEncodingError, decode_bytes, read_text

SAMPLE = "Data;Opis;Kwota\n2026-05-15;Opłata za zarządzanie;-42,42\n"


@pytest.mark.parametrize(
    "raw, encoding, had_bom",
    [
        (codecs.BOM_UTF8 + SAMPLE.encode("utf-8"), "utf-8-sig", True),
        (SAMPLE.encode("utf-8"), "utf-8", False),
        (SAMPLE.encode("utf-16"), "utf-16", True),
        (SAMPLE.encode("cp1250"), "cp1250", False),
    ],
)
def test_detects_encoding(raw, encoding, had_bom):
    decoded = decode_bytes(raw)
    assert decoded.text == SAMPLE
    assert decoded.encoding == encoding
    assert decoded.had_bom is had_bom


def test_bom_is_not_part_of_text():
    decoded = decode_bytes(codecs.BOM_UTF8 + b"Data;Kwota\n")
    assert decoded.text.startswith("Data")


def test_binary_is_rejected():
    with pytest.raises(UnknownEncodingError):
        decode_bytes(b"PK\x03\x04\x00\x81binary")


def test_read_text_from_file(tmp_path):
    path = tmp_path / "export.csv"
    path.write_bytes(codecs.BOM_UTF8 + SAMPLE.encode("utf-8"))
    assert read_text(path).text == SAMPLE
