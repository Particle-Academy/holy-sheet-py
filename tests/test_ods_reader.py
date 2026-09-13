"""Ported from `holy-sheet/tests/Unit/OdsReaderTest.php`, on the same fixture bytes.

The fixtures are loaded from the PHP repo (see `_oracle.ods_fixtures_dir`), and
what the PHP file says about each of them applies here.

PHP's `toBe` checks key ORDER and exact types; Python's `==` on dicts checks
neither order nor `1 == 1.0 == True`. `_same` compares JSON, which catches all
three -- including the bool-is-an-int trap this repo's AGENTS.md warns about.

edge.ods is asserted cell by cell in PHP and diffed against PHP wholesale in
`test_ods_reader_parity_php.py`; the traps a Python port falls into on it are
repeated below so they fail here even without PHP on the path.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest

import holy_sheet
from holy_sheet import UnsupportedFormatException
from tests import _oracle

_NAMESPACES = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"'
)


def _same(actual: Any, expected: Any) -> None:
    assert json.dumps(actual, indent=1, ensure_ascii=False) == json.dumps(expected, indent=1, ensure_ascii=False)


def _fixture(name: str) -> dict[str, Any]:
    return holy_sheet.describe(str(_oracle.ods_fixtures_dir() / name))


def _package(tables: str, automatic_styles: str = "", mimetype: str = "application/vnd.oasis.opendocument.spreadsheet") -> bytes:
    """A minimal .ods from table XML. Mirrors the PHP test's ods_package()."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {_NAMESPACES} office:version="1.3">'
        f"<office:automatic-styles>{automatic_styles}</office:automatic-styles>"
        f"<office:body><office:spreadsheet>{tables}</office:spreadsheet></office:body>"
        "</office:document-content>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), mimetype)
        archive.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def _without_auto(schema: dict[str, Any]) -> dict[str, Any]:
    """The xlsx reader reports `displayFormat: auto` everywhere; ods invents none. See the PHP test."""
    for sheet in schema["sheets"]:
        for cell in sheet["cells"].values():
            fmt = cell.get("format")
            if fmt is not None and fmt.get("displayFormat") == "auto":
                del fmt["displayFormat"]
                if not fmt:
                    del cell["format"]
    return schema


# --- the xlsx-converted fixture ---------------------------------------------


def test_reads_every_value_type_from_a_libreoffice_written_ods() -> None:
    cells = _fixture("workbook.ods")["sheets"][0]["cells"]

    _same(cells["A2"], {"value": 100, "comment": {"text": "An integer", "author": "Fixture"}})
    _same(cells["B2"], {"value": 9800.5})
    _same(cells["C2"], {"value": -42})
    _same(cells["A3"], {"value": 0.25, "format": {"displayFormat": "percentage", "decimals": 1}})
    _same(cells["B3"], {"value": 1234.5, "format": {"displayFormat": "currency", "decimals": 2, "currency": "USD"}})
    _same(cells["C3"], {"value": 99, "format": {"displayFormat": "currency", "decimals": 0, "currency": "EUR"}})
    _same(cells["A4"], {"value": "2024-03-15", "format": {"displayFormat": "date"}})
    _same(cells["B4"], {"value": "2024-03-15T10:30:00Z", "format": {"displayFormat": "datetime"}})
    _same(cells["C4"], {"value": 1234.5678, "format": {"displayFormat": "number", "decimals": 2}})
    _same(cells["A5"], {"value": True})
    _same(cells["B5"], {"value": False})
    _same(cells["A6"], {"value": "first line\nsecond line"})
    _same(cells["B6"], {"value": "spaced   out"})
    _same(cells["C6"], {"value": "Fish & Chips <tasty>"})


def test_reads_cell_formatting_from_automatic_styles_parents_and_column_defaults() -> None:
    sheets = _fixture("workbook.ods")["sheets"]

    _same(sheets[0]["cells"]["A1"], {"value": "Kind", "format": {"bold": True}})
    _same(sheets[0]["cells"]["B1"], {"value": "Value", "format": {"bold": True, "italic": True, "textAlign": "center"}})
    _same(
        sheets[0]["cells"]["C1"],
        {"value": "Styled", "format": {"color": "#1D4ED8", "backgroundColor": "#FEF3C7", "fontSize": 14, "borderBottom": "#111827"}},
    )
    _same(sheets[2]["cells"]["A1"], {"value": "Across", "format": {"bold": True}})


def test_translates_openformula_into_the_a1_syntax_the_xlsx_reader_returns() -> None:
    cells = _fixture("workbook.ods")["sheets"][0]["cells"]

    _same(cells["A7"], {"value": None, "formula": "SUM(A2:C2)", "computedValue": 9858.5})
    _same(cells["B7"], {"value": None, "formula": "'Other Sheet'!A1*2", "computedValue": 42})
    _same(cells["C7"], {"value": None, "formula": 'IF(A5,LEN("a;b"),0)', "computedValue": 3})
    _same(cells["D7"], {"value": None, "formula": "ROUND(B2/4,1)", "computedValue": 2450.1})


def test_expands_repeated_cells_and_rows_without_inventing_empty_ones() -> None:
    cells = _fixture("workbook.ods")["sheets"][1]["cells"]

    _same(list(cells), ["A1", "B1", "C1", "D1", "E1", "A2", "B2", "A3", "B3", "A4", "B4", "H20"])
    _same(cells["E1"], {"value": 7})
    _same(cells["H20"], {"value": "far"})


def test_reads_merges_sheet_names_an_empty_sheet_and_document_metadata() -> None:
    schema = _fixture("workbook.ods")

    _same([s["name"] for s in schema["sheets"]], ["Types", "Repeats", "Merged", "Other Sheet", "Empty"])
    _same(schema["sheets"][2]["mergedRegions"], [{"start": "A1", "end": "C1"}, {"start": "A2", "end": "A3"}])
    _same(schema["sheets"][2]["cells"]["B3"], {"value": 2})
    _same(schema["sheets"][4], {"name": "Empty", "cells": {}})
    _same(schema["meta"], {"creator": "Holy Sheet ODS fixture", "created": "2026-09-13T12:00:00Z"})


def test_describes_the_same_workbook_the_same_way_whether_saved_as_xlsx_or_ods() -> None:
    from_xlsx = _without_auto(_fixture("workbook.xlsx"))
    from_ods = _fixture("workbook.ods")

    # Frozen panes are view settings a headless conversion does not write.
    del from_xlsx["sheets"][0]["frozenRows"], from_xlsx["sheets"][0]["frozenCols"]

    _same(from_ods, from_xlsx)


def test_returns_a_schema_that_writes_straight_back_out() -> None:
    schema = _fixture("workbook.ods")
    assert holy_sheet.validate(schema) == []
    back = _without_auto(holy_sheet.read(holy_sheet.to_bytes(schema)))
    _same(back["sheets"][0]["cells"]["B3"], schema["sheets"][0]["cells"]["B3"])


# --- the hand-authored native fixture ---------------------------------------


def test_reads_native_values_time_currency_runs_links_tabs_and_breaks() -> None:
    schema = _fixture("native.ods")
    cells = schema["sheets"][0]["cells"]

    _same(
        cells["A1"],
        {
            "value": "Hello bold and a link",
            "format": {
                "bold": True, "italic": True, "color": "#F8FAFC", "backgroundColor": "#0F172A", "fontSize": 16,
                "borderTop": "#334155", "borderRight": "#334155", "borderBottom": "#334155", "borderLeft": "#334155",
            },
        },
    )
    _same(cells["B1"], {"value": "1899-12-30T10:30:00Z", "format": {"displayFormat": "datetime"}})
    _same(cells["C1"], {"value": 19.99, "format": {"textAlign": "right", "displayFormat": "currency", "decimals": 2, "currency": "EUR"}})
    _same(cells["D1"], {"value": 0.125, "format": {"displayFormat": "percentage", "decimals": 2}})
    _same(cells["A2"], {"value": False})
    _same(cells["B2"], {"value": "1999-12-31", "format": {"displayFormat": "date"}})
    _same(cells["C2"], {"value": "a\tb\nc", "comment": {"text": "First note paragraph\nSecond note paragraph"}})
    _same(cells["D2"], {"value": 1500})
    _same(schema["meta"], {"creator": "Holy Sheet native ODS fixture", "created": "2026-09-13T08:15:00Z"})


def test_translates_sheet_references_absolute_cells_quoted_strings_and_inline_arrays() -> None:
    cells = _fixture("native.ods")["sheets"][0]["cells"]

    _same(cells["A3"], {"value": None, "formula": "SUM(Data!A1:A3)", "computedValue": 6})
    _same(cells["B3"], {"value": None, "formula": "$D$2*2", "computedValue": 3000})
    _same(cells["C3"], {"value": None, "formula": 'CONCATENATE("x;y","""q""")', "computedValue": 'x;y"q"'})
    _same(cells["D3"], {"value": None, "formula": "SUMPRODUCT({1,2;3,4},{1,1;1,1})", "computedValue": 10})


# --- edge.ods: the traps a Python port falls into ---------------------------


def test_rounds_half_a_second_away_from_zero_not_to_even() -> None:
    # round(0.5) == 0 and round(2.5) == 2 in Python; PHP says 1 and 3.
    cells = _fixture("edge.ods")["sheets"][4]["cells"]

    _same(cells["D1"], {"value": "2025-01-01T00:00:00Z", "format": {"displayFormat": "datetime"}})
    _same(cells["E1"], {"value": "1899-12-30T00:00:03Z", "format": {"displayFormat": "datetime"}})
    _same(cells["I1"], {"value": 1500.0})


def test_style_elements_with_no_children_still_count() -> None:
    # An Element with no children is falsy, so `a.get(k) or b.get(k)` skips an
    # empty style and finds the wrong one.
    cells = _fixture("edge.ods")["sheets"][0]["cells"]

    _same(cells["D1"], {"value": 1234.5, "format": {"displayFormat": "auto"}})
    _same(cells["B3"], {"value": "unknown style"})
    _same(cells["M1"], {"value": "loop", "format": {"bold": True, "italic": True}})
    _same(cells["N1"], {"value": -5, "format": {"displayFormat": "currency", "decimals": 2, "currency": "USD"}})


def test_matches_attributes_by_namespace_uri() -> None:
    cells = _fixture("edge.ods")["sheets"]

    _same(cells[1]["cells"]["I1"], {"value": 42})
    _same(cells[2]["cells"]["C1"], {"value": None, "formula": "#REF!+1", "computedValue": "#REF!"})


def test_collapses_white_space_and_reads_mixed_content_in_order() -> None:
    cells = _fixture("edge.ods")["sheets"][1]["cells"]

    _same(cells["A1"], {"value": "leading and inner tabs newline "})
    _same(cells["B1"], {"value": "a   b   "})
    _same(cells["C1"], {"value": "Heading\nbody span nested"})
    _same(cells["D1"], {"value": "xyz ☺ & <raw>"})


# --- constructs LibreOffice normalises away, built in memory ----------------


def test_expands_rows_repeated_with_content_and_skips_a_trailing_million_row_repeat() -> None:
    schema = holy_sheet.read(
        _package(
            '<table:table table:name="R">'
            '<table:table-row table:number-rows-repeated="3">'
            '<table:table-cell office:value-type="float" office:value="5"/>'
            '<table:table-cell table:number-columns-repeated="2"/>'
            '<table:table-cell office:value-type="string"><text:p>tail</text:p></table:table-cell>'
            "</table:table-row>"
            '<table:table-row table:number-rows-repeated="1048573"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>'
            "</table:table>"
        )
    )

    _same(
        schema["sheets"][0]["cells"],
        {
            "A1": {"value": 5}, "D1": {"value": "tail"},
            "A2": {"value": 5}, "D2": {"value": "tail"},
            "A3": {"value": 5}, "D3": {"value": "tail"},
        },
    )


def test_reads_rows_inside_header_row_and_row_groups_in_document_order() -> None:
    schema = holy_sheet.read(
        _package(
            '<table:table table:name="G">'
            '<table:table-header-rows><table:table-row><table:table-cell office:value-type="string"><text:p>head</text:p></table:table-cell></table:table-row></table:table-header-rows>'
            '<table:table-row-group><table:table-row><table:table-cell office:value-type="float" office:value="1"/></table:table-row>'
            '<table:table-row-group><table:table-row><table:table-cell office:value-type="float" office:value="2"/></table:table-row></table:table-row-group>'
            "</table:table-row-group>"
            '<table:table-row><table:table-cell office:value-type="float" office:value="3"/></table:table-row>'
            "</table:table>"
        )
    )

    _same(schema["sheets"][0]["cells"], {"A1": {"value": "head"}, "A2": {"value": 1}, "A3": {"value": 2}, "A4": {"value": 3}})


def test_keeps_content_in_a_covered_cell_and_records_both_span_directions() -> None:
    schema = holy_sheet.read(
        _package(
            '<table:table table:name="M"><table:table-row>'
            '<table:table-cell table:number-columns-spanned="2" table:number-rows-spanned="2" office:value-type="string"><text:p>big</text:p></table:table-cell>'
            '<table:covered-table-cell office:value-type="string"><text:p>hidden</text:p></table:covered-table-cell>'
            "</table:table-row></table:table>"
        )
    )

    _same(schema["sheets"][0]["cells"], {"A1": {"value": "big"}, "B1": {"value": "hidden"}})
    _same(schema["sheets"][0]["mergedRegions"], [{"start": "A1", "end": "B2"}])


def test_reports_a_formula_result_of_every_type_as_the_xlsx_reader_would_cache_it() -> None:
    cells = holy_sheet.read(
        _package(
            '<table:table table:name="F"><table:table-row>'
            '<table:table-cell table:formula="of:=DATE(2024;3;15)" office:value-type="date" office:date-value="2024-03-15"/>'
            '<table:table-cell table:formula="of:=[.A1]+0.5" office:value-type="date" office:date-value="2024-03-15T12:00:00"/>'
            '<table:table-cell table:formula="of:=TIME(6;0;0)" office:value-type="time" office:time-value="PT6H"/>'
            '<table:table-cell table:formula="of:=1=1" office:value-type="boolean" office:boolean-value="true"/>'
            '<table:table-cell table:formula="of:=&quot;a&quot;" office:value-type="string"><text:p>a</text:p></table:table-cell>'
            "</table:table-row></table:table>"
        )
    )["sheets"][0]["cells"]

    _same(cells["A1"], {"value": None, "formula": "DATE(2024,3,15)", "computedValue": 45366, "format": {"displayFormat": "date"}})
    _same(cells["B1"], {"value": None, "formula": "A1+0.5", "computedValue": 45366.5, "format": {"displayFormat": "datetime"}})
    _same(cells["C1"], {"value": None, "formula": "TIME(6,0,0)", "computedValue": 0.25, "format": {"displayFormat": "datetime"}})
    _same(cells["D1"], {"value": None, "formula": "1=1", "computedValue": True})
    _same(cells["E1"], {"value": None, "formula": '"a"', "computedValue": "a"})


def test_passes_excel_syntax_through_and_translates_the_older_openoffice_prefix() -> None:
    cells = holy_sheet.read(
        _package(
            '<table:table table:name="P"><table:table-row>'
            '<table:table-cell table:formula="msoxl:=SUM(A1:B2,C3)" office:value-type="float" office:value="0"/>'
            '<table:table-cell table:formula="oooc:=SUM([.A1:.B2];[.C3])" office:value-type="float" office:value="0"/>'
            '<table:table-cell table:formula="of:=SUM([&apos;It&apos;&apos;s here&apos;.A1:.A2];[$Other.$B$1:.$B$9])" office:value-type="float" office:value="0"/>'
            "</table:table-row></table:table>"
        )
    )["sheets"][0]["cells"]

    assert cells["A1"]["formula"] == "SUM(A1:B2,C3)"
    assert cells["B1"]["formula"] == "SUM(A1:B2,C3)"
    assert cells["C1"]["formula"] == "SUM('It''s here'!A1:A2,Other!$B$1:$B$9)"


def test_converts_date_and_time_values_exactly() -> None:
    cells = holy_sheet.read(
        _package(
            '<table:table table:name="D"><table:table-row>'
            '<table:table-cell office:value-type="time" office:time-value="PT36H15M30S"/>'
            '<table:table-cell office:value-type="date" office:date-value="2024-01-01T23:59:59.6"/>'
            '<table:table-cell office:value-type="date" office:date-value="2024-06-01T10:00:00+02:00"/>'
            '<table:table-cell office:value-type="string"><text:p/></table:table-cell>'
            "<table:table-cell><text:p>untyped</text:p></table:table-cell>"
            "</table:table-row></table:table>"
        )
    )["sheets"][0]["cells"]

    _same(cells["A1"], {"value": "1899-12-31T12:15:30Z", "format": {"displayFormat": "datetime"}})
    _same(cells["B1"], {"value": "2024-01-02T00:00:00Z", "format": {"displayFormat": "datetime"}})
    _same(cells["C1"], {"value": "2024-06-01T08:00:00Z", "format": {"displayFormat": "datetime"}})
    _same(cells["D1"], {"value": ""})
    _same(cells["E1"], {"value": "untyped"})


# --- dispatch ---------------------------------------------------------------


def test_still_describes_xlsx_through_the_same_entry_point() -> None:
    assert _fixture("workbook.xlsx")["sheets"][0]["cells"]["A2"]["value"] == 100


def test_names_what_it_cannot_read_instead_of_failing_on_a_zip_it_does_not_know() -> None:
    data = _package('<table:table table:name="X"/>', "", "application/vnd.oasis.opendocument.text")

    with pytest.raises(UnsupportedFormatException, match="application/vnd.oasis.opendocument.text") as caught:
        holy_sheet.read(data)
    assert caught.value.mimetype == "application/vnd.oasis.opendocument.text"


def test_refuses_bytes_that_are_not_a_zip_and_the_exception_is_still_a_runtime_error() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        holy_sheet.read(b"a,b\n1,2\n")
    assert isinstance(caught.value, RuntimeError)
