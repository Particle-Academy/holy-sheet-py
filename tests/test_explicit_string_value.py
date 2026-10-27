"""An explicit ``{"value": <string>}`` cell is a STRING cell, written exactly as given.

The object form is how a caller says "I mean this literally" (it is already the
documented escape hatch for a leading "="). Numeric-string coercion ran on it
anyway, so ``{"value": "+1"}`` was written as the number 1 and
``{"value": "007"}`` as 7. Bare strings keep their promotion rules unchanged.

Mirrors PHP ``tests/Unit/ExplicitStringValueTest.php`` and Node
``tests/explicit-string-value.test.ts``; keep the three in step.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

import pytest

import holy_sheet
from holy_sheet.schema.normalizer import Normalizer

NUMERIC_LOOKING = ["+1", "007", "1e5", "-0", "1.50", " 12 ", "0.0", "-3"]


def _cells(schema: dict[str, Any]) -> dict[str, Any]:
    return Normalizer().normalize(schema).sheets[0].cells


def _written(schema: dict[str, Any], ref: str) -> tuple[str, dict[str, Any]]:
    data = holy_sheet.to_bytes(schema)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    m = re.search(rf'<c r="{ref}"[ >].*?(?:/>|</c>)', sheet)
    return (m.group(0) if m else f"(no {ref})"), holy_sheet.read(data)


@pytest.mark.parametrize("s", NUMERIC_LOOKING)
def test_an_explicit_string_in_a_cells_map_is_a_string_cell(s: str) -> None:
    schema = {"sheets": [{"name": "S", "cells": {"A1": {"value": s}}}]}

    assert _cells(schema)["A1"].value == s

    xml, read = _written(schema, "A1")
    assert 't="inlineStr"' in xml
    assert f'<t xml:space="preserve">{s}</t>' in xml
    value = read["sheets"][0]["cells"]["A1"]["value"]
    assert isinstance(value, str) and value == s


@pytest.mark.parametrize("s", NUMERIC_LOOKING)
def test_an_explicit_string_in_a_row_is_a_string_cell(s: str) -> None:
    schema = {"sheets": [{"name": "S", "columns": [{"header": "Code"}], "rows": [[{"value": s}]]}]}

    assert _cells(schema)["A2"].value == s

    xml, read = _written(schema, "A2")
    assert 't="inlineStr"' in xml
    value = read["sheets"][0]["cells"]["A2"]["value"]
    assert isinstance(value, str) and value == s


def test_an_explicit_string_stays_a_string_under_a_numeric_type_and_format() -> None:
    cells = _cells(
        {
            "sheets": [
                {
                    "name": "S",
                    "columns": [{"header": "N", "type": "number"}, {"header": "C", "type": "currency"}],
                    "rows": [[{"value": "+1"}, {"value": "007", "format": {"displayFormat": "currency"}}]],
                }
            ]
        }
    )

    assert cells["A2"].value == "+1"
    assert cells["B2"].value == "007"


def test_an_explicit_leading_equals_string_is_not_a_formula() -> None:
    cell = _cells({"sheets": [{"name": "S", "cells": {"A1": {"value": "=1+1"}}}]})["A1"]

    assert cell.formula is None
    assert cell.value == "=1+1"


def test_bare_strings_explicit_numbers_and_dates_are_unchanged() -> None:
    cells = _cells(
        {
            "sheets": [
                {
                    "name": "S",
                    "cells": {
                        "A1": "+1",
                        "A2": "=SUM(B1:B2)",
                        "A3": {"value": 1},
                        "A4": {"value": "2024-03-15", "format": {"displayFormat": "date"}},
                    },
                }
            ]
        }
    )

    assert cells["A1"].value == 1 and not isinstance(cells["A1"].value, str)
    assert cells["A2"].formula == "SUM(B1:B2)"
    assert cells["A3"].value == 1
    assert cells["A4"].value == 45366

    rows = _cells({"sheets": [{"name": "S", "columns": [{"header": "N"}], "rows": [["+1"]]}]})
    assert rows["A2"].value == 1 and not isinstance(rows["A2"].value, str)
