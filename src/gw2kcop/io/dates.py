"""Parse dates and times from exports and convert them to Europe/Warsaw.

Formats are named explicitly by the caller (from the format's TOML), never guessed:
``05/06/2026`` is 5 June in Amundi's portal and 6 May in its PDF statement.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

# Named formats -> strptime patterns
DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD.MM.YYYY": "%d.%m.%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "MM/DD/YYYY": "%m/%d/%Y",
}
DATETIME_FORMATS = {
    "YYYY-MM-DD HH:MM:SS": "%Y-%m-%d %H:%M:%S",
    "YYYY-MM-DD HH:MM": "%Y-%m-%d %H:%M",
    "DD.MM.YYYY HH:MM:SS": "%d.%m.%Y %H:%M:%S",
    "DD.MM.YYYY HH:MM": "%d.%m.%Y %H:%M",
    "DD-MM-YYYY HH:MM:SS": "%d-%m-%Y %H:%M:%S",
    "DD/MM/YYYY HH:MM:SS": "%d/%m/%Y %H:%M:%S",
}


def parse_date(text: str, fmt: str) -> date:
    """Parse a date written in the named format ``fmt`` (e.g. ``"DD.MM.YYYY"``)."""
    try:
        pattern = DATE_FORMATS[fmt]
    except KeyError:
        raise ValueError(f"unknown date format: {fmt!r}") from None
    return datetime.strptime(text.strip(), pattern).date()


def parse_datetime(text: str, fmt: str, source_tz: str = "Europe/Warsaw") -> datetime:
    """Parse a timestamp in the named format and return it aware, in Europe/Warsaw.

    ``source_tz`` is the time zone the export uses (e.g. ``"UTC"`` for XTB).
    """
    try:
        pattern = DATETIME_FORMATS[fmt]
    except KeyError:
        raise ValueError(f"unknown datetime format: {fmt!r}") from None
    naive = datetime.strptime(text.strip(), pattern)
    return to_warsaw(naive.replace(tzinfo=ZoneInfo(source_tz)))


def to_warsaw(moment: datetime) -> datetime:
    """Convert an aware datetime to Europe/Warsaw. Naive input is rejected."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime: the source time zone must be known")
    return moment.astimezone(WARSAW)
