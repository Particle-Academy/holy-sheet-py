"""The PHP-semantics layer, pinned directly.

Everything here is a rule Python gets WRONG by default, so each test is really
asserting "we did not reach for the builtin". They are cheap and they are the
first thing to check when a cross-runtime diff appears.
"""

from __future__ import annotations

import math

import pytest

from holy_sheet.helpers.php import (
    format_float,
    is_numeric_string,
    numeric_string_to_number,
    php_float_to_string,
    php_number_format,
    php_round,
    php_to_string,
    type_of,
)


class TestPhpRound:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0.5, 1.0), (1.5, 2.0), (2.5, 3.0), (-0.5, -1.0), (-1.5, -2.0), (-2.5, -3.0)],
    )
    def test_rounds_half_away_from_zero(self, value: float, expected: float) -> None:
        assert php_round(value) == expected

    def test_the_builtin_would_have_disagreed(self) -> None:
        """The whole reason this helper exists.

        Python's `round` is BANKER'S rounding. If these ever agree, someone has
        replaced php_round with the builtin and every EMU conversion, column
        width and ROUND() call in the family has silently changed.
        """
        assert round(0.5) == 0 and php_round(0.5) == 1.0
        assert round(2.5) == 2 and php_round(2.5) == 3.0

    def test_rounds_at_a_precision_the_way_a_caller_means_it(self) -> None:
        """1.2345 rounds to 1.235, matching PHP.

        The exact double behind the literal `1.2345` is 1.23449999999999993, so
        quantising the binary expansion would give 1.234 -- technically true and
        nobody's intent. PHP gets to 1.235 via a floating-point pre-round;
        php_round quantises the shortest repr, which is the same answer stated
        directly.
        """
        assert php_round(1.2345, 3) == 1.235
        assert php_round(-1.2345, 3) == -1.235
        assert php_round(0.285, 2) == 0.29


class TestNumericStrings:
    @pytest.mark.parametrize(
        "text", ["1e5", "1E5", "1.5e3", "2e-3", "007", ".5", "-3.25", " 12 ", "1.", "+5"]
    )
    def test_accepts_what_php_accepts(self, text: str) -> None:
        assert is_numeric_string(text)

    @pytest.mark.parametrize("text", ["0x1A", "1_000", "inf", "nan", "Infinity", "", "abc", "12abc"])
    def test_rejects_what_php_rejects(self, text: str) -> None:
        """Every one of these is accepted by Python's `float()`.

        `float("inf")` and `float("nan")` produce values with no `<v>`
        representation at all, and `float("1_000")` silently reads a thousand
        separator as a number. PHP's is_numeric says no to all of them.
        """
        assert not is_numeric_string(text)

    def test_a_bool_is_not_numeric(self) -> None:
        """`isinstance(True, int)` is True; is_numeric_string must not be."""
        assert not is_numeric_string(True)
        assert not is_numeric_string(False)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1e5", 100000.0), ("1.5e3", 1500.0), ("2e-3", 0.002), ("007", 7), (".5", 0.5)],
    )
    def test_coerces_the_way_php_does(self, text: str, expected: float | int) -> None:
        assert numeric_string_to_number(text) == expected

    def test_integer_shaped_strings_become_ints(self) -> None:
        assert isinstance(numeric_string_to_number("007"), int)
        assert isinstance(numeric_string_to_number("1e5"), float)

    def test_does_not_clamp_past_the_php_int_range(self) -> None:
        """`(int) "1e21"` is PHP_INT_MAX -- a number nobody wrote.

        The dot-test coercion both engines used to share took the int branch for
        any string without a ".", so every magnitude past the int range became
        9223372036854775807 on PHP and 1 on Node.
        """
        assert numeric_string_to_number("1e21") == 1e21
        assert numeric_string_to_number("99999999999999999999") == 1e20
        assert numeric_string_to_number("9223372036854775808") == float(2**63)


class TestNumberFormat:
    def test_rounds_half_away_from_zero_at_the_fourteenth_decimal(self) -> None:
        """The exact ties -- j / 2**15 with odd j.

        These are the only doubles whose decimal expansion terminates in a 5 at
        the 15th place, so they are the only inputs where half-even and
        half-away-from-zero can disagree at all. Both values are in the
        `numericHazards` parity fixture; PHP writes 813 and 438 for them.
        """
        assert php_number_format(3.0517578125e-05, 14) == "0.00003051757813"
        assert php_number_format(9.1552734375e-05, 14) == "0.00009155273438"

    def test_the_builtin_formatter_would_have_disagreed(self) -> None:
        """`f"{v:.14f}"` rounds half to EVEN, and here that is one digit low.

        Only half the ties discriminate -- half-even lands on 438 by itself for
        the second value, because 8 is the even neighbour. The first one is the
        test: 812 vs 813.
        """
        assert f"{3.0517578125e-05:.14f}" == "0.00003051757812"
        assert php_number_format(3.0517578125e-05, 14) == "0.00003051757813"

    def test_suppresses_the_sign_on_a_zero_result(self) -> None:
        """PHP's number_format(-0.0, 14) has no minus sign; Python's format does."""
        assert php_number_format(-0.0, 14) == "0.00000000000000"
        assert f"{-0.0:.14f}" == "-0.00000000000000"

    def test_formats_column_widths_at_four_places(self) -> None:
        assert php_number_format(16.428571428571427, 4) == "16.4286"
        assert php_number_format(10.714285714285714, 4) == "10.7143"


class TestFormatFloat:
    def test_trims_only_a_fraction(self) -> None:
        assert format_float(0.1) == "0.1"
        assert format_float(1500.0) == "1500"
        assert format_float(0.002) == "0.002"
        assert format_float(1 / 3) == "0.33333333333333"

    def test_expands_large_magnitudes_instead_of_going_exponential(self) -> None:
        """The bug that shipped in the Node port: 1e300 written as `1e3`.

        `toFixed` switches to exponential at 1e21 and the trailing-zero trim
        then chewed the EXPONENT. Not a crash, not invalid XML -- a different
        number, in a file someone opens later and believes.
        """
        assert format_float(1e21) == "1" + "0" * 21
        expanded = format_float(1e300)
        assert expanded.startswith("1000000000000000052504760255204420248704")
        assert len(expanded) == 301
        assert "e" not in expanded and "E" not in expanded

    def test_never_writes_a_non_finite_value(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            assert format_float(value) == "0"

    def test_writes_zero_not_negative_zero(self) -> None:
        assert format_float(-0.0) == "0"


class TestPhpToString:
    def test_bools_are_php_bools_not_python_words(self) -> None:
        """`str(True)` is "True", which would land the word True in a cell."""
        assert php_to_string(True) == "1"
        assert php_to_string(False) == ""

    def test_none_is_the_empty_string(self) -> None:
        assert php_to_string(None) == ""

    def test_floats_use_phps_precision_14_rendering(self) -> None:
        assert php_float_to_string(0.1) == "0.1"
        assert php_float_to_string(1 / 3) == "0.33333333333333"
        assert php_float_to_string(1e21) == "1.0E+21"
        assert php_float_to_string(1500.0) == "1500"


class TestTypeOf:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, "null"),
            (True, "bool"),
            (False, "bool"),
            (1, "int"),
            (1.5, "float"),
            ("x", "string"),
            ([], "array"),
            ({}, "object"),
        ],
    )
    def test_names_types_the_way_php_does(self, value: object, expected: str) -> None:
        assert type_of(value) == expected

    def test_a_bool_is_never_reported_as_an_int(self) -> None:
        assert type_of(True) == "bool"
