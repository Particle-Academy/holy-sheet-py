"""Ported from `holy-sheet/tests/Unit/StylesTest.php`."""

from __future__ import annotations

import io
import re
import zipfile

import holy_sheet


def _parts(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name).decode() for name in archive.namelist()}


def test_every_workbook_carries_a_styles_part() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {"sheets": [{"name": "X", "columns": [{"header": "A"}], "rows": [[1]]}]}
        )
    )

    assert "xl/styles.xml" in parts
    styles = parts["xl/styles.xml"]
    for needle in ("<styleSheet", "<fonts", "<fills", "<borders", "<cellXfs"):
        assert needle in styles, f"missing {needle} in styles.xml"


def test_emits_a_bold_coloured_header_style_under_the_default_theme() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [{"header": "A"}, {"header": "B"}],
                        "rows": [[1, 2]],
                        "theme": "default",
                    }
                ]
            }
        )
    )

    assert "<b/>" in parts["xl/styles.xml"], "expected a bold font for the header"
    assert re.search(r'<c r="A1" s="\d+"', parts["xl/worksheets/sheet1.xml"])


def test_applies_a_currency_number_format_on_a_currency_column() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [
                            {"header": "Amount", "type": "currency", "currency": "USD"}
                        ],
                        "rows": [[1234.56]],
                        "theme": "plain",
                    }
                ]
            }
        )
    )

    assert "&quot;$&quot;#,##0.00" in parts["xl/styles.xml"]


def test_applies_a_percent_format_on_a_percent_column() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [{"header": "Rate", "type": "percent", "decimals": 2}],
                        "rows": [[0.124]],
                        "theme": "plain",
                    }
                ]
            }
        )
    )

    assert "0.00%" in parts["xl/styles.xml"]


def test_converts_iso_date_strings_to_excel_serial_numbers() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [{"header": "When", "type": "date"}],
                        "rows": [["2026-05-01"]],
                        "theme": "plain",
                    }
                ]
            }
        )
    )

    # 2026-05-01 is 46143 days after the 1899-12-30 anchor.
    assert "<v>46143</v>" in parts["xl/worksheets/sheet1.xml"]


def test_emits_merged_regions_frozen_panes_and_column_widths() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "cells": {"A1": {"value": "Hello"}},
                        "mergedRegions": [{"start": "A1", "end": "C1"}],
                        "frozenRows": 1,
                        "frozenCols": 0,
                        "columnWidths": {0: 200},
                    }
                ]
            }
        )
    )

    sheet = parts["xl/worksheets/sheet1.xml"]
    assert '<mergeCell ref="A1:C1"/>' in sheet
    assert "<sheetView" in sheet
    assert 'ySplit="1"' in sheet
    assert '<col min="1" max="1"' in sheet
    # (200 - 5) / 7 = 27.857142857142858, formatted at four places.
    assert 'width="27.8571"' in sheet


def test_writes_the_symbolic_totals_row_with_sum_and_average_formulas() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [
                            {"header": "Region"},
                            {"header": "Revenue", "type": "number"},
                            {"header": "YoY", "type": "percent"},
                        ],
                        "rows": [["A", 100, 0.1], ["B", 200, 0.2], ["C", 300, 0.3]],
                        "totals": {"Revenue": "sum", "YoY": "avg"},
                        "theme": "plain",
                    }
                ]
            }
        )
    )

    sheet = parts["xl/worksheets/sheet1.xml"]
    assert "<f>SUM(B2:B4)</f>" in sheet
    assert "<f>AVERAGE(C2:C4)</f>" in sheet


def test_ignores_an_unknown_aggregation_in_totals() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [{"header": "N", "type": "number"}],
                        "rows": [[1], [2]],
                        "totals": {"N": "median"},
                        "theme": "plain",
                    }
                ]
            }
        )
    )

    assert "<f>" not in parts["xl/worksheets/sheet1.xml"]


def test_writes_comments_xml_and_a_vml_drawing_when_a_cell_has_a_comment() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "cells": {
                            "A1": {
                                "value": "Hello",
                                "comment": {"text": "Note this cell", "author": "Agent"},
                            }
                        },
                    }
                ]
            }
        )
    )

    assert "xl/comments1.xml" in parts
    assert "xl/drawings/vmlDrawing1.vml" in parts
    assert "xl/worksheets/_rels/sheet1.xml.rels" in parts
    assert "Note this cell" in parts["xl/comments1.xml"]
    assert "<author>Agent</author>" in parts["xl/comments1.xml"]


def test_emits_cached_formula_values_when_computed_value_is_provided() -> None:
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "cells": {
                            "A1": {"value": 10},
                            "A2": {"value": 20},
                            "A3": {"formula": "SUM(A1:A2)", "computedValue": 30},
                        },
                    }
                ]
            }
        )
    )

    assert "<f>SUM(A1:A2)</f><v>30</v>" in parts["xl/worksheets/sheet1.xml"]


def test_dedupes_identical_formats_into_one_xf_record() -> None:
    """The reason the registry exists: styles.xml must not grow with row count."""
    rows = [[f"r{i}"] for i in range(200)]
    parts = _parts(
        holy_sheet.to_bytes(
            {
                "sheets": [
                    {
                        "name": "X",
                        "columns": [{"header": "A"}],
                        "rows": rows,
                        "theme": "default",
                    }
                ]
            }
        )
    )

    count = int(re.search(r'<cellXfs count="(\d+)"', parts["xl/styles.xml"]).group(1))
    # base + header + banded-row fill, and nothing per-row beyond that.
    assert count == 3
