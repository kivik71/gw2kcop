"""Shared file input used by all parsers.

- ``text``: read a file as text with encoding/BOM detection (fail closed on unknown bytes)
- ``csvio``: detect the delimiter and read CSV rows
- ``numbers``: parse PL/EU formatted numbers into ``Decimal``
- ``dates``: parse export dates/times and convert them to Europe/Warsaw
"""

from gw2kcop.io.csvio import read_csv_rows, sniff_delimiter
from gw2kcop.io.dates import WARSAW, parse_date, parse_datetime, to_warsaw
from gw2kcop.io.numbers import parse_decimal, parse_optional_decimal
from gw2kcop.io.text import DecodedText, read_text

__all__ = [
    "WARSAW",
    "DecodedText",
    "parse_date",
    "parse_datetime",
    "parse_decimal",
    "parse_optional_decimal",
    "read_csv_rows",
    "read_text",
    "sniff_delimiter",
    "to_warsaw",
]
