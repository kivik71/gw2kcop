from decimal import Decimal

import pytest

from gw2kcop.io.numbers import parse_decimal, parse_optional_decimal


@pytest.mark.parametrize(
    "text, expected",
    [
        ("-4372,04", "-4372.04"),
        ("0,625", "0.625"),
        ("1 039,94", "1039.94"),
        ("1 039,94", "1039.94"),
        ("1 039,94", "1039.94"),
        ("1.165,76", "1165.76"),
        ("22 542,62", "22542.62"),
        ("12,431927", "12.431927"),
        ("+2,50", "2.50"),
        ("−5,00", "-5.00"),
        ("5000", "5000"),
        ("PLN 41 602,04", "41602.04"),
        ("187,25 €", "187.25"),
        ("-42,42 zł", "-42.42"),
    ],
)
def test_comma_decimal(text, expected):
    assert parse_decimal(text, ",") == Decimal(expected)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("+ 187.25 €", "187.25"),
        ("9 852.70", "9852.70"),
        ("1,234.5", "1234.5"),
        ("EUR 10 501.81", "10501.81"),
        ("259.00", "259.00"),
    ],
)
def test_dot_decimal(text, expected):
    assert parse_decimal(text, ".") == Decimal(expected)


def test_thousands_separator_without_decimals():
    # "1.165" in a comma-decimal export is one thousand one hundred sixty-five.
    assert parse_decimal("1.165", ",") == Decimal("1165")


@pytest.mark.parametrize(
    "text, sep",
    [
        ("", ","),
        ("abc", ","),
        ("1,2,3", ","),
        ("12.34,5", ","),        # bad grouping
        ("1 000.000,5", ","),    # mixed thousands separators
        ("1.5", ","),            # dot not allowed as decimal in a comma format
        ("1,5", "."),
        ("--5", ","),
        ("5-", ","),
        ("1e5", "."),
        ("NaN", "."),
    ],
)
def test_rejects_unexpected(text, sep):
    with pytest.raises(ValueError):
        parse_decimal(text, sep)


def test_unsupported_separator():
    with pytest.raises(ValueError):
        parse_decimal("1", ";")


@pytest.mark.parametrize("text", [None, "", "  ", "-", "–"])
def test_optional_empty(text):
    assert parse_optional_decimal(text) is None


def test_optional_value():
    assert parse_optional_decimal("2,43") == Decimal("2.43")
