"""``columnWidths`` entries that are not a column index and a width.

Before 0.3.2 the three writers disagreed: this one raised ``ValueError`` from
``to_bytes()`` (``int("abc")``), PHP overwrote column A, and Node wrote a NaN
column. One rule now, mirroring PHP holy-sheet 2.3.4's tests.
"""

from __future__ import annotations

import pytest

import holy_sheet
from holy_sheet import SchemaException
from holy_sheet.schema.normalizer import Normalizer


def _sheet(widths):
    return {"sheets": [{"name": "S", "cells": {"A1": {"value": "x"}}, "columnWidths": widths}]}


def test_a_key_that_is_not_a_column_index_neither_crashes_nor_overwrites_a_column():
    workbook = Normalizer().normalize(_sheet({"0": 120, "abc": 999, "1": 80}))

    assert workbook.sheets[0].column_widths == {0: 120.0, 1: 80.0}


def test_every_bad_entry_is_reported_by_path_instead_of_written():
    errors = holy_sheet.validate(
        _sheet({"abc": 50, "-1": 50, "16384": 50, "B": 50, "0": "wide", "1": -5})
    )

    assert [e["path"] for e in errors] == [
        "sheets[0].columnWidths.abc",
        "sheets[0].columnWidths.-1",
        "sheets[0].columnWidths.16384",
        "sheets[0].columnWidths.B",
        "sheets[0].columnWidths.0",
        "sheets[0].columnWidths.1",
    ]

    with pytest.raises(SchemaException):
        holy_sheet.to_bytes(_sheet({"abc": 999}))


def test_indexes_widths_and_a_list_that_are_valid_stay_valid():
    assert holy_sheet.validate(_sheet({0: 120, "1": "80", "2": 140.5, "16383": 10})) == []
    # PHP encodes widths keyed 0..n-1 as a JSON list; that is a valid map.
    assert holy_sheet.validate(_sheet([120, 80])) == []
    # A bool is not a width, even though True == 1 in Python.
    assert holy_sheet.validate(_sheet({"0": True})) != []


def test_a_letter_key_is_repaired_to_its_index_and_the_rest_dropped():
    result = holy_sheet.validate_and_repair(_sheet({"B": 90, "abc": 5, "0": "wide", "2": 60}))

    assert result["errors"] == []
    assert result["schema"]["sheets"][0]["columnWidths"] == {1: 90, 2: 60}
    assert "converted 'sheets[0].columnWidths.B' to column index 1" in result["repairs"]
    assert "dropped 'sheets[0].columnWidths.abc' (not a column index and a width)" in result["repairs"]
    assert "dropped 'sheets[0].columnWidths.0' (not a column index and a width)" in result["repairs"]
