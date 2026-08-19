"""Excel serial number -> ISO-8601 string; the companion to `DateConverter`.

Anchored to 1899-12-30, matching Excel's 1900 leap-year quirk for every date
after 1900-03-01. A fractional day encodes time-of-day; `include_time` decides
whether it reaches the output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ...helpers.php import php_round

_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)


class DateInverter:
    @staticmethod
    def to_iso(serial: float | int, include_time: bool = False) -> str:
        # php_round, not the builtin: Python's round() is half-to-even, so a
        # serial landing exactly on a half-second would tip the wrong way and
        # -- at the day boundary -- print the wrong DATE.
        seconds = int(php_round(float(serial) * 86400))
        moment = _EPOCH + timedelta(seconds=seconds)
        if include_time:
            return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        return moment.strftime("%Y-%m-%d")
