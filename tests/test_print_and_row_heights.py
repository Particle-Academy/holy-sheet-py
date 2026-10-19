"""Print setup and row heights.

Mirrors the PHP engine's PrintAndRowHeightsTest. Byte parity with PHP is the
``printSetup`` entry in ``tests/fixtures.py``.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from holy_sheet import read, to_bytes, validate


def _schema(**extra: Any) -> dict[str, Any]:
    return {"sheets": [{"name": "Q3", "cells": {"A1": "Title", "B2": 42}, **extra}]}


def _sheet_xml(schema: dict[str, Any]) -> str:
    with zipfile.ZipFile(io.BytesIO(to_bytes(schema))) as z:
        return z.read("xl/worksheets/sheet1.xml").decode("utf-8")


def test_fits_to_one_page_wide_and_picks_the_orientation() -> None:
    xml = _sheet_xml(_schema(print={"orientation": "landscape", "fitToWidth": True}))
    assert '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>' in xml
    assert '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>' in xml


def test_prints_a_footer_with_live_page_codes_escaping_a_literal_ampersand() -> None:
    xml = _sheet_xml(_schema(print={"footer": {"left": "Q3 Review & Forecast", "right": "Page {page} of {pages}"}}))
    assert (
        "<headerFooter><oddFooter>&amp;LQ3 Review &amp;&amp; Forecast&amp;RPage &amp;P of &amp;N</oddFooter></headerFooter>"
        in xml
    )


def test_row_heights_in_pixels_on_rows_with_and_without_cells() -> None:
    xml = _sheet_xml(_schema(rowHeights={"1": 32, "3": 20}))
    assert '<row r="1" ht="24" customHeight="1">' in xml
    assert '<row r="3" ht="15" customHeight="1"/>' in xml


def test_nothing_added_when_neither_is_asked_for() -> None:
    xml = _sheet_xml(_schema())
    assert "sheetPr" not in xml and "pageSetup" not in xml and "customHeight" not in xml


def test_both_read_back_as_written() -> None:
    extra = {
        "rowHeights": {"1": 32, "3": 20},
        "print": {"orientation": "landscape", "fitToWidth": True, "footer": {"left": "Q3 & Q4", "center": "Confidential", "right": "Page {page} of {pages}"}},
    }
    sheet = read(to_bytes(_schema(**extra)))["sheets"][0]
    assert sheet["rowHeights"] == {1: 32, 3: 20}
    assert sheet["print"] == extra["print"]


def test_malformed_print_and_row_heights_are_refused() -> None:
    errors = validate(
        _schema(rowHeights={"0": 20, "2": -1}, print={"orientation": "sideways", "fitToWidth": "yes", "footer": {"left": 7}})
    )
    paths = {e["path"] for e in errors}
    assert {
        "sheets[0].rowHeights.0",
        "sheets[0].rowHeights.2",
        "sheets[0].print.orientation",
        "sheets[0].print.fitToWidth",
        "sheets[0].print.footer.left",
    } <= paths


def test_fits_a_short_sheet_onto_one_page_so_a_chart_below_the_table_is_never_cut() -> None:
    xml = _sheet_xml(_schema(print={"orientation": "portrait", "fitToPage": True}))
    assert '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>' in xml
    assert '<pageSetup orientation="portrait" fitToWidth="1" fitToHeight="1"/>' in xml

    got = read(to_bytes(_schema(print={"fitToPage": True, "footer": {"right": "Page {page} of {pages}"}})))["sheets"][0]["print"]
    assert got == {"fitToPage": True, "footer": {"right": "Page {page} of {pages}"}}

    assert [e["path"] for e in validate(_schema(print={"fitToPage": 1}))] == ["sheets[0].print.fitToPage"]
