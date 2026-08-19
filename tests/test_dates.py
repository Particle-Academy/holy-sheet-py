"""The date grammar, pinned explicitly.

The polyglot parity ruling for this family is: **define an explicit accepted
format list, parse in UTC, and reject everything else.** Both existing engines
fail that in opposite directions -- PHP accepts anything `DateTimeImmutable`
does (including `"next monday"`, which nothing else in the family can
reproduce), and Node falls through to `Date.parse`, which reads a bare datetime
as LOCAL time and is a live off-by-one-day defect west of UTC.

So the grammar is written down here rather than delegated. In particular it is
NOT delegated to `datetime.fromisoformat`, whose accepted set changed in Python
3.11 -- that would make the serial in a cell depend on the interpreter version.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from holy_sheet.reader.format.date_inverter import DateInverter
from holy_sheet.writer.format.date_converter import DateConverter


class TestAcceptedGrammar:
    @pytest.mark.parametrize(
        ("text", "serial"),
        [
            ("2026-05-01", 46143.0),
            ("2024-03-15", 45366.0),
            ("1900-03-01", 61.0),
            ("2024/03/15", 45366.0),
            ("2024-3-5", 45356.0),
        ],
    )
    def test_parses_a_date(self, text: str, serial: float) -> None:
        assert DateConverter.to_serial(text) == serial

    @pytest.mark.parametrize(
        "text",
        [
            "2024-03-15T12:00:00Z",
            "2024-03-15 12:00:00",
            "2024-03-15T12:00",
            "2024-03-15T12:00:00.500Z",
        ],
    )
    def test_parses_a_datetime(self, text: str) -> None:
        assert DateConverter.to_serial(text, True) == 45366.5

    def test_applies_a_utc_offset(self) -> None:
        assert DateConverter.to_serial("2024-03-15T12:00:00+02:00", True) == 45366.0 + 10 / 24
        assert DateConverter.to_serial("2024-03-15T12:00:00-0200", True) == 45366.0 + 14 / 24

    def test_a_bare_datetime_is_utc_not_local(self) -> None:
        """Node's `Date.parse` fallback reads this as local time.

        West of UTC that shifts the serial across midnight and the cell shows
        the previous day -- a defect that only reproduces on the maintainer's
        machine if the maintainer happens to live east of Greenwich.
        """
        assert DateConverter.to_serial("2024-03-15T00:00:00", True) == 45366.0

    def test_accepts_a_python_date_or_datetime(self) -> None:
        assert DateConverter.to_serial(date(2024, 3, 15)) == 45366.0
        assert (
            DateConverter.to_serial(datetime(2024, 3, 15, 12, tzinfo=timezone.utc), True)
            == 45366.5
        )

    def test_drops_the_time_when_include_time_is_false(self) -> None:
        assert DateConverter.to_serial("2024-03-15T18:45:00Z") == 45366.0

    def test_always_returns_a_float(self) -> None:
        """The writer's float branch is what renders `<v>46143</v>`."""
        assert isinstance(DateConverter.to_serial("2026-05-01"), float)


class TestRejectedGrammar:
    @pytest.mark.parametrize(
        "text",
        ["next monday", "15/03/2024", "March 15 2024", "", "   ", "not a date", "2024"],
    )
    def test_unparseable_input_is_serial_zero(self, text: str) -> None:
        """PHP's grammar is unportable and its failure path is serial 0.

        Deliberately stricter, and deliberately silent in the same way the
        reference is -- `"next monday"` parses in PHP and nowhere else, so
        accepting it would be a Python-only behaviour rather than parity.
        """
        assert DateConverter.to_serial(text) == 0.0

    def test_an_impossible_date_is_serial_zero(self) -> None:
        assert DateConverter.to_serial("2024-02-30") == 0.0
        assert DateConverter.to_serial("2024-13-01") == 0.0


class TestInverse:
    @pytest.mark.parametrize("text", ["2026-05-01", "2024-03-15", "1990-01-01"])
    def test_round_trips_a_date(self, text: str) -> None:
        assert DateInverter.to_iso(DateConverter.to_serial(text)) == text

    def test_round_trips_a_datetime(self) -> None:
        serial = DateConverter.to_serial("2024-03-15T12:30:45Z", True)
        assert DateInverter.to_iso(serial, True) == "2024-03-15T12:30:45Z"

    def test_the_epoch_is_the_1899_12_30_anchor(self) -> None:
        """Not 1900-01-01, and the offset is Excel's leap-year bug.

        Excel believes 1900 was a leap year, so every serial after 1900-03-01 is
        one higher than a correct calendar would give. Anchoring two days early
        cancels it: serial 61 is 1900-03-01, which is what Excel shows, and
        2026-05-01 comes out as 46143 -- the number the PHP suite pins.
        """
        assert DateInverter.to_iso(0.0) == "1899-12-30"
        assert DateInverter.to_iso(61.0) == "1900-03-01"
        assert DateInverter.to_iso(46143.0) == "2026-05-01"
