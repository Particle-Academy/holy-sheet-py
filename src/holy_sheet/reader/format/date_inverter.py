"""Excel serial number -> ISO-8601 string; the companion to `DateConverter`.

Anchored to 1899-12-30, matching Excel's 1900 leap-year quirk for every date
after 1900-03-01. A fractional day encodes time-of-day; `include_time` decides
whether it reaches the output.

A workbook that declares `<workbookPr date1904="1"/>` counts from 1904-01-01
instead (the old Mac default), so the same serial is a date 1462 days later;
`date1904` selects that epoch. Mirrors PHP `DateInverter::toIso`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...helpers.php import php_round

_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)
_EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)


class DateInverter:
    @staticmethod
    def to_iso(serial: float | int, include_time: bool = False, date1904: bool = False) -> str:
        # php_round, not the builtin: Python's round() is half-to-even, so a
        # serial landing exactly on a half-second would tip the wrong way and
        # -- at the day boundary -- print the wrong DATE.
        seconds = int(php_round(float(serial) * 86400))
        moment = (_EPOCH_1904 if date1904 else _EPOCH) + timedelta(seconds=seconds)
        if include_time:
            return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        return moment.strftime("%Y-%m-%d")
