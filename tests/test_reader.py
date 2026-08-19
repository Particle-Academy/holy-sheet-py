"""Ported from `holy-sheet/tests/Unit/ReaderTest.php`.

The reader's contract is `write -> read -> the same schema`. Every test here is
a round trip except the shared-strings one, which reads a hand-built package
Excel would produce and this writer never does.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import holy_sheet


def _round_trip(schema: dict) -> dict:
    return holy_sheet.read(holy_sheet.to_bytes(schema))


def test_describes_a_simple_workbook_back_to_schema() -> None:
    out = _round_trip(
        {
            "sheets": [
                {
                    "name": "Q1",
                    "columns": [
                        {"header": "Region", "type": "string"},
                        {"header": "Revenue", "type": "currency", "currency": "USD"},
                    ],
                    "rows": [["NA", 100], ["EU", 200]],
                }
            ]
        }
    )

    sheet = out["sheets"][0]
    assert sheet["name"] == "Q1"
    assert "A1" in sheet["cells"]
    assert sheet["cells"]["A1"]["value"] == "Region"
    assert sheet["cells"]["B2"]["value"] == 100


def test_describe_returns_not_found_for_a_missing_path() -> None:
    assert holy_sheet.describe("/nope/missing.xlsx") == {
        "error": "not_found",
        "path": "/nope/missing.xlsx",
    }


def test_describe_reads_a_file_from_disk(tmp_path) -> None:
    path = tmp_path / "book.xlsx"
    holy_sheet.write({"sheets": [{"name": "D", "cells": {"A1": {"value": 7}}}]}, str(path))

    out = holy_sheet.describe(str(path))

    assert out["sheets"][0]["cells"]["A1"]["value"] == 7


def test_round_trips_merged_regions() -> None:
    out = _round_trip(
        {
            "sheets": [
                {
                    "name": "M",
                    "rows": [["a", "b"]],
                    "mergedRegions": [{"start": "A1", "end": "B1"}],
                }
            ]
        }
    )

    assert out["sheets"][0]["mergedRegions"][0] == {"start": "A1", "end": "B1"}


def test_round_trips_frozen_panes() -> None:
    out = _round_trip(
        {"sheets": [{"name": "F", "rows": [["x"]], "frozenRows": 1, "frozenCols": 2}]}
    )

    assert out["sheets"][0]["frozenRows"] == 1
    assert out["sheets"][0]["frozenCols"] == 2


def test_round_trips_column_widths() -> None:
    out = _round_trip(
        {"sheets": [{"name": "W", "cells": {"A1": {"value": 1}}, "columnWidths": {0: 120}}]}
    )

    assert out["sheets"][0]["columnWidths"] == {0: 120.0}


def test_round_trips_formulas_with_cached_values() -> None:
    out = _round_trip(
        {
            "sheets": [
                {"name": "C", "rows": [[1, 2, {"formula": "A1+B1", "computedValue": 3}]]}
            ]
        }
    )

    cell = out["sheets"][0]["cells"]["C1"]
    assert cell["formula"] == "A1+B1"
    assert cell["computedValue"] == 3


def test_round_trips_comments() -> None:
    out = _round_trip(
        {
            "sheets": [
                {
                    "name": "N",
                    "cells": {
                        "A1": {"value": "hi", "comment": {"text": "note", "author": "me"}}
                    },
                }
            ]
        }
    )

    assert out["sheets"][0]["cells"]["A1"]["comment"]["text"] == "note"
    assert out["sheets"][0]["cells"]["A1"]["comment"]["author"] == "me"


def test_round_trips_a_date_cell_back_to_its_iso_string() -> None:
    out = _round_trip(
        {
            "sheets": [
                {
                    "name": "D",
                    "cells": {
                        "A1": {"value": "2024-03-15", "format": {"displayFormat": "date"}}
                    },
                }
            ]
        }
    )

    cell = out["sheets"][0]["cells"]["A1"]
    assert cell["value"] == "2024-03-15"
    assert cell["format"]["displayFormat"] == "date"


def test_round_trips_formatting() -> None:
    out = _round_trip(
        {
            "sheets": [
                {
                    "name": "S",
                    "cells": {
                        "A1": {
                            "value": "Title",
                            "format": {
                                "bold": True,
                                "fontSize": 14,
                                "color": "#FF0000",
                                "textAlign": "center",
                            },
                        }
                    },
                }
            ]
        }
    )

    fmt = out["sheets"][0]["cells"]["A1"]["format"]
    assert fmt["bold"] is True
    assert fmt["fontSize"] == 14
    assert fmt["color"] == "#FF0000"
    assert fmt["textAlign"] == "center"


def test_the_described_schema_can_be_written_again() -> None:
    """The round trip has to be a fixpoint, or `describe` is a dead end."""
    original = {
        "sheets": [
            {
                "name": "Q1",
                "cells": {"A1": {"value": "Region"}, "B1": {"value": 12000}},
            }
        ]
    }

    once = holy_sheet.read(holy_sheet.to_bytes(original))
    twice = holy_sheet.read(holy_sheet.to_bytes(once))

    assert once == twice


def test_resolves_shared_string_references() -> None:
    """This writer emits inline strings, but Excel emits a shared-strings table.

    A `t="s"` cell is meaningless without it, so the reader has to handle a
    package it will never itself produce -- including a rich string split across
    several runs.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">'
            "<si><t>Revenue Category</t></si>"
            "<si><t>Subscription</t></si>"
            "<si><r><t>Rich </t></r><r><t>String</t></r></si>"
            "</sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c></row></sheetData></worksheet>',
        )

    cells = holy_sheet.read(buffer.getvalue())["sheets"][0]["cells"]

    assert cells["A1"]["value"] == "Revenue Category"
    assert cells["B1"]["value"] == "Subscription"
    assert cells["A2"]["value"] == "Rich String"


def test_refuses_a_part_carrying_a_doctype() -> None:
    """An xlsx never legitimately contains one.

    An internal DTD subset is the entry point for entity expansion on untrusted
    input, and a reader handed arbitrary uploaded files should not depend on
    what its parser happens to do with one today.
    """
    from holy_sheet.reader.xml import XmlError, parse_xml

    with pytest.raises(XmlError, match="DOCTYPE"):
        parse_xml(b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x "boom">]><t>&x;</t>')


def test_rejects_input_that_is_not_a_zip() -> None:
    with pytest.raises(RuntimeError, match="zip archive"):
        holy_sheet.read(b"not a zip at all")
