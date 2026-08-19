"""ISO string / datetime -> Excel serial number.

Excel's epoch is 1900-01-01 carrying the legacy "1900 was a leap year" bug (it
was not). Anchoring to 1899-12-30 reproduces Excel's arithmetic for every date
from 1900-03-01 on, which is every date an agent realistically writes.

**Parsing is deliberately stricter than the PHP reference.** PHP accepts
anything `DateTimeImmutable` accepts, which includes `"next monday"` and a
locale-flavoured long tail that no other runtime in this family can reproduce;
the polyglot parity plan's ruling is an explicit accepted-format list, parsed in
**UTC**. So the grammar below is written out rather than delegated -- including
to `datetime.fromisoformat`, whose accepted set CHANGED in Python 3.11 and would
otherwise make the package's behaviour depend on the interpreter version.

Anything outside the list yields serial 0.0, matching what PHP does when its own
parse fails.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone

_EPOCH = datetime(1899, 12, 30, tzinfo=timezone.utc)

# YYYY-MM-DD  |  YYYY/MM/DD, optionally followed by a time, optionally followed
# by a zone. `T` or a single space separates date from time (both are common in
# agent output and in Excel exports).
_DATETIME = re.compile(
    r"""^
    (?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})
    (?:
        [T ]
        (?P<hour>\d{1,2}):(?P<minute>\d{2})
        (?::(?P<second>\d{2})(?:\.(?P<fraction>\d+))?)?
        \s*
        (?P<zone>Z|z|[+-]\d{2}:?\d{2}|[+-]\d{2})?
    )?
    $""",
    re.VERBOSE,
)


class DateConverter:
    @staticmethod
    def to_serial(value: str | datetime | date, include_time: bool = False) -> float:
        parsed = _to_utc(value)
        if parsed is None:
            return 0.0

        # Whole seconds, because PHP subtracts two `getTimestamp()` values and
        # that truncates sub-second precision. Carrying microseconds here would
        # put a different serial in the cell for the same input.
        seconds = int((parsed - _EPOCH).total_seconds())
        days = seconds / 86400
        if not include_time:
            days = math.floor(days)
        # Always a float: PHP's `floor()` returns one too, and the writer's
        # float branch (number_format + trim) is what produces `<v>46143</v>`.
        return float(days)


def _to_utc(value: str | datetime | date) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(
            tzinfo=timezone.utc
        )
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if text == "":
        return None
    match = _DATETIME.match(text)
    if match is None:
        return None

    parts = match.groupdict()
    fraction = parts["fraction"] or ""
    microsecond = int((fraction + "000000")[:6]) if fraction else 0
    try:
        naive = datetime(
            int(parts["year"]),
            int(parts["month"]),
            int(parts["day"]),
            int(parts["hour"] or 0),
            int(parts["minute"] or 0),
            int(parts["second"] or 0),
            microsecond,
        )
    except ValueError:
        return None

    zone = parts["zone"]
    if zone in (None, "", "Z", "z"):
        # No zone means UTC. Node's reference reader falls back to
        # `Date.parse`, which reads a bare datetime as LOCAL time and is a live
        # off-by-one-day defect west of UTC; the plan rules against it.
        return naive.replace(tzinfo=timezone.utc)

    sign = 1 if zone[0] == "+" else -1
    digits = zone[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) > 2 else 0
    return (naive - sign * timedelta(hours=hours, minutes=minutes)).replace(
        tzinfo=timezone.utc
    )
