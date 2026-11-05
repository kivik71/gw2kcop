from datetime import date, datetime, timezone

import pytest

from gw2kcop.io.dates import WARSAW, parse_date, parse_datetime, to_warsaw


@pytest.mark.parametrize(
    "text, fmt, expected",
    [
        ("2026-10-12", "YYYY-MM-DD", date(2026, 10, 12)),
        ("13.10.2026", "DD.MM.YYYY", date(2026, 10, 13)),
        ("15-05-2026", "DD-MM-YYYY", date(2026, 5, 15)),
        ("05/06/2026", "DD/MM/YYYY", date(2026, 6, 5)),
        ("05/06/2026", "MM/DD/YYYY", date(2026, 5, 6)),
    ],
)
def test_parse_date(text, fmt, expected):
    assert parse_date(text, fmt) == expected


def test_parse_date_rejects_wrong_format():
    with pytest.raises(ValueError):
        parse_date("2026-10-12", "DD.MM.YYYY")


def test_unknown_format_name():
    with pytest.raises(ValueError):
        parse_date("2026-10-12", "YYYY/MM/DD")


def test_utc_summer_to_warsaw():
    moment = parse_datetime("2026-07-01 22:30:00", "YYYY-MM-DD HH:MM:SS", source_tz="UTC")
    assert moment.tzinfo is WARSAW
    assert (moment.date(), moment.hour) == (date(2026, 7, 2), 0)  # UTC+2


def test_utc_winter_to_warsaw():
    moment = parse_datetime("2026-01-15 23:30:00", "YYYY-MM-DD HH:MM:SS", source_tz="UTC")
    assert (moment.date(), moment.hour) == (date(2026, 1, 16), 0)  # UTC+1


def test_local_time_stays():
    moment = parse_datetime("15-05-2026 11:00:00", "DD-MM-YYYY HH:MM:SS")
    assert (moment.hour, moment.utcoffset().total_seconds()) == (11, 7200)


def test_to_warsaw_rejects_naive():
    with pytest.raises(ValueError):
        to_warsaw(datetime(2026, 1, 1, 12, 0))


def test_to_warsaw_aware():
    assert to_warsaw(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)).hour == 13
