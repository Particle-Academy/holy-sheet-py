"""Ported from `holy-sheet/tests/Unit/NumericTest.php`, at the cell surface.

The polyglot conformance policy names these hazards; each one was a live
PHP<->JS disagreement in a shipped package, and none was covered. The parity
suites diff whole OOXML parts, so they only catch a divergence if some fixture
happens to contain the offending value -- none did. Numbers are the thing a
spreadsheet writer exists to get right, so they get their own table instead of
waiting to be caught incidentally.

`tests/fixtures.py` now carries these values too, which makes the same table a
cross-runtime assertion rather than two per-engine tables that happen to agree.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from typing import Any

import pytest

import holy_sheet


def cell_xml_for(value: Any) -> str:
    """The `<c r="A1" …>` element for a single-cell workbook."""
    data = holy_sheet.to_bytes({"sheets": [{"name": "S", "rows": [[value]]}]})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    match = re.search(r'<c r="A1".*?(?:/>|</c>)', sheet)
    return match.group(0) if match else "(no A1)"


def test_does_not_clamp_a_numeric_string_past_the_php_int_range() -> None:
    """`(int) "1e21"` is PHP_INT_MAX, and `parseInt("1e21")` is 1.

    Two engines, two different wrong numbers, from the same input. The float
    path is what both now take.
    """
    xml = cell_xml_for("1e21")

    assert "<v>1000000000000000000000</v>" in xml
    assert "9223372036854775807" not in xml


def test_does_not_clamp_a_long_integer_string() -> None:
    assert "9223372036854775807" not in cell_xml_for("99999999999999999999")


def test_reads_exponent_notation_in_numeric_strings() -> None:
    """PHP has parsed exponents in numeric strings since PHP 7.

    Python's `float()` agrees -- but only once `is_numeric_string` has let the
    value through, which is the part that has to be written down: `float()` also
    accepts "inf", "nan" and "1_000", and none of those is a number PHP would
    have seen.
    """
    assert "<v>100000</v>" in cell_xml_for("1e5")
    assert "<v>1500</v>" in cell_xml_for("1.5e3")
    assert "<v>0.002</v>" in cell_xml_for("2e-3")


def test_keeps_the_plain_cases_exactly_where_they_were() -> None:
    assert "<v>7</v>" in cell_xml_for("007")
    assert "<v>0.5</v>" in cell_xml_for(".5")
    assert "<v>-3.25</v>" in cell_xml_for("-3.25")
    assert "<v>42</v>" in cell_xml_for(42)
    assert "<v>1.5</v>" in cell_xml_for(1.5)


def test_writes_zero_not_negative_zero() -> None:
    """PHP's `number_format(-0.0, 14)` carries no sign; Python's format does."""
    assert "<v>0</v>" in cell_xml_for(-0.0)
    assert "<v>-0</v>" not in cell_xml_for(-0.0)


def test_expands_a_large_float_instead_of_writing_an_exponent() -> None:
    """`<v>1e+21</v>` is not valid cell content -- Excel will not read it."""
    assert "<v>1000000000000000000000</v>" in cell_xml_for(1e21)

    xml = cell_xml_for(1e300)
    assert "e+" not in xml.lower()
    assert "<v>1000000000000000052504760255204420248704" in xml


def test_never_writes_a_non_finite_value_into_a_cell() -> None:
    """"NaN" in a cell is a corrupt sheet, not a big number."""
    for value in (math.nan, math.inf, -math.inf):
        assert re.search(r"nan|inf", cell_xml_for(value), re.IGNORECASE) is None


def test_a_bool_never_lands_in_a_numeric_cell() -> None:
    """Python-specific, and the one that would have shipped silently.

    `isinstance(True, int)` is True and `True == 1`, so any type branch that
    tests int before bool writes `<v>1</v>` with no `t="b"`.
    """
    assert 't="b"' in cell_xml_for(True)
    assert 't="b"' in cell_xml_for(False)
    assert '<c r="A1"><v>1</v></c>' not in cell_xml_for(True)


def test_rounds_ties_at_the_fourteenth_decimal_away_from_zero() -> None:
    """`f"{v:.14f}"` would write ...812 here. PHP writes ...813."""
    assert "<v>0.00003051757813</v>" in cell_xml_for(3.0517578125e-05)


def test_an_int_beyond_the_php_int_range_is_written_as_a_float() -> None:
    """Python's ints are unbounded; PHP's are 64-bit and json_decode floats them.

    Left alone, a Python caller would put the exact decimal 10**25 in a cell,
    which no other engine in the family can produce: PHP's `json_decode` hands
    that literal back as the double 1.0E+25, whose exact expansion ends
    ...905969664. Matching PHP means losing the extra precision on purpose.
    """
    assert "<v>10000000000000000905969664</v>" in cell_xml_for(10**25)
    assert cell_xml_for(2**63 - 1) == '<c r="A1"><v>9223372036854775807</v></c>'


@pytest.mark.parametrize("value", ["inf", "nan", "1_000", "0x1A"])
def test_python_only_numeric_strings_stay_text(value: str) -> None:
    """Every one of these is accepted by `float()` and rejected by is_numeric."""
    assert 't="inlineStr"' in cell_xml_for(value)
