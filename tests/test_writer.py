"""Ported from `holy-sheet/tests/Unit/WriterTest.php` (+ the container rules).

The PHP suite writes to a temp file and asserts the archive's shape; this does
the same, and adds the checks that only matter for a THIRD implementation --
the fixed part order and the fixed zip timestamp.
"""

from __future__ import annotations

import zipfile

import pytest

import holy_sheet
from tests.fixtures import SCHEMAS

MANDATORY_PARTS = [
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/worksheets/sheet1.xml",
    "docProps/core.xml",
    "docProps/app.xml",
]

# The order the writer emits them in -- fixed, and NOT the same as the list
# above. Worksheets come last because their styles must all be registered before
# styles.xml is serialised.
EMISSION_ORDER = [
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/styles.xml",
    "docProps/core.xml",
    "docProps/app.xml",
    "xl/worksheets/sheet1.xml",
]


def _open(data: bytes) -> zipfile.ZipFile:
    import io

    return zipfile.ZipFile(io.BytesIO(data))


def test_writes_a_minimum_viable_xlsx(tmp_path) -> None:
    schema = {
        "sheets": [
            {
                "name": "Sheet 1",
                "columns": [{"header": "Name"}, {"header": "Age", "type": "integer"}],
                "rows": [["Alice", 30], ["Bob", 42]],
            }
        ]
    }
    path = tmp_path / "book.xlsx"

    result = holy_sheet.write(schema, str(path))

    assert result["path"] == str(path)
    assert result["sheets"] == 1
    assert result["bytes"] > 0
    assert path.read_bytes()[:4] == b"PK\x03\x04"


def test_writes_a_workbook_with_multiple_sheets(tmp_path) -> None:
    schema = {
        "sheets": [
            {"name": "A", "columns": [{"header": "X"}], "rows": [[1], [2]]},
            {"name": "B", "columns": [{"header": "Y"}], "rows": [[3], [4]]},
        ]
    }
    path = tmp_path / "two.xlsx"

    assert holy_sheet.write(schema, str(path))["sheets"] == 2

    with _open(path.read_bytes()) as archive:
        assert "xl/worksheets/sheet2.xml" in archive.namelist()


def test_accepts_the_fancy_sheets_style_sparse_cells_map() -> None:
    data = holy_sheet.to_bytes(
        {
            "sheets": [
                {
                    "name": "Sparse",
                    "cells": {
                        "A1": {"value": "Header"},
                        "A2": {"value": 100},
                        "B2": {"value": 200},
                        "A3": {"formula": "SUM(A2:B2)"},
                    },
                }
            ]
        }
    )

    with _open(data) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>SUM(A2:B2)</f>" in sheet


def test_the_produced_xlsx_contains_every_mandatory_part() -> None:
    data = holy_sheet.to_bytes(SCHEMAS["minimal"])

    with _open(data) as archive:
        names = archive.namelist()
    for part in MANDATORY_PARTS:
        assert part in names, f"missing required xlsx entry: {part}"


def test_the_part_order_is_fixed() -> None:
    """Emission order is part of the contract, not an implementation detail.

    A reader does not care, but the parity suites compare part SETS in order and
    a golden fixture is worthless if the container reshuffles between runs.
    """
    with _open(holy_sheet.to_bytes(SCHEMAS["minimal"])) as archive:
        assert archive.namelist() == EMISSION_ORDER


def test_the_zip_carries_a_fixed_timestamp() -> None:
    """No clock in the container -- otherwise the bytes change every run."""
    with _open(holy_sheet.to_bytes(SCHEMAS["minimal"])) as archive:
        for info in archive.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)


def test_writes_no_shared_strings_or_calc_chain() -> None:
    """The three engines emit exactly these parts and no others.

    `sharedStrings.xml` and `calcChain.xml` are the classic sources of xlsx diff
    noise, and every engine in this family deliberately writes inline strings
    instead.
    """
    with _open(holy_sheet.to_bytes(SCHEMAS["rowOriented"])) as archive:
        names = archive.namelist()
    assert "xl/sharedStrings.xml" not in names
    assert "xl/calcChain.xml" not in names
    assert "xl/theme/theme1.xml" not in names


def test_escapes_xml_metacharacters_in_cell_text_and_sheet_names() -> None:
    data = holy_sheet.to_bytes(
        {"sheets": [{"name": "A & B", "cells": {"A1": {"value": "<x> & \"y\" & 'z'"}}}]}
    )

    with _open(data) as archive:
        workbook = archive.read("xl/workbook.xml").decode()
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()

    assert 'name="A &amp; B"' in workbook
    # `&apos;`, not `&#39;` -- a library escaper picks the other one and breaks
    # parts parity on the first fixture.
    assert "&lt;x&gt; &amp; &quot;y&quot; &amp; &apos;z&apos;" in sheet


def test_strips_control_characters_that_xml_cannot_carry() -> None:
    data = holy_sheet.to_bytes(
        {"sheets": [{"name": "S", "cells": {"A1": {"value": "a\x00b\x1fc"}}}]}
    )

    with _open(data) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert ">abc<" in sheet


def test_writes_a_self_closing_cell_for_a_null_value() -> None:
    data = holy_sheet.to_bytes({"sheets": [{"name": "S", "cells": {"A1": {"value": None}}}]})

    with _open(data) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="A1"/>' in sheet


def test_writes_booleans_as_typed_boolean_cells() -> None:
    """The `bool`-is-an-`int` trap, at the surface where it would show.

    If the type branch tested int before bool, TRUE would go in as `<v>1</v>`
    with no `t="b"` -- a sheet that shows 1 where the author wrote TRUE.
    """
    data = holy_sheet.to_bytes(
        {"sheets": [{"name": "S", "cells": {"A1": {"value": True}, "A2": {"value": False}}}]}
    )

    with _open(data) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="A1" t="b"><v>1</v></c>' in sheet
    assert '<c r="A2" t="b"><v>0</v></c>' in sheet


def test_orders_cells_within_a_row_lexicographically_past_column_z() -> None:
    """A shared WART, pinned so a port cannot quietly fix it.

    Both shipped engines sort the column letters as strings, so a wide row emits
    A, AA, AB, …, B, C. Excel tolerates it. Changing it would change the bytes
    of every wide sheet in every engine at once, so it belongs in a coordinated
    release, not here -- and `tests/test_parity_php.py` proves PHP still agrees.
    """
    with _open(holy_sheet.to_bytes(SCHEMAS["wide"])) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()

    import re

    order = re.findall(r'<c r="([A-Z]+)1"', sheet)
    assert order[:6] == ["A", "AA", "AB", "AC", "AD", "B"]
    assert order == sorted(order)


def test_raises_rather_than_writing_an_invalid_schema(tmp_path) -> None:
    path = tmp_path / "never.xlsx"
    with pytest.raises(holy_sheet.SchemaException):
        holy_sheet.write({"sheets": []}, str(path))
    assert not path.exists()
