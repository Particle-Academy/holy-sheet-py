"""Ported from `holy-sheet/tests/Unit/RepairerTest.php`."""

from __future__ import annotations

import holy_sheet


def test_renames_singular_sheet_to_sheets() -> None:
    result = holy_sheet.validate_and_repair({"sheet": [{"name": "A", "rows": []}]})

    assert "sheets" in result["schema"]
    assert "sheet" not in result["schema"]
    assert result["repairs"] != []


def test_wraps_a_single_sheet_object_in_a_list() -> None:
    result = holy_sheet.validate_and_repair({"sheet": {"name": "A", "rows": []}})

    assert result["schema"]["sheets"] == [{"name": "A", "rows": []}]


def test_renames_row_to_rows() -> None:
    result = holy_sheet.validate_and_repair(
        {"sheets": [{"name": "A", "row": [["x"]]}]}
    )

    assert "rows" in result["schema"]["sheets"][0]
    assert "row" not in result["schema"]["sheets"][0]


def test_converts_an_integer_keyed_rows_object_to_an_indexed_list() -> None:
    result = holy_sheet.validate_and_repair(
        {"sheets": [{"name": "A", "rows": {"0": ["a"], "1": ["b"]}}]}
    )

    assert result["schema"]["sheets"][0]["rows"] == [["a"], ["b"]]


def test_leaves_a_non_integer_keyed_rows_object_alone() -> None:
    """Ambiguous input is an ERROR to surface, not a shape to guess at."""
    result = holy_sheet.validate_and_repair(
        {"sheets": [{"name": "A", "rows": {"first": ["a"]}}]}
    )

    assert result["schema"]["sheets"][0]["rows"] == {"first": ["a"]}


def test_coerces_stringified_numerics_in_number_columns() -> None:
    result = holy_sheet.validate_and_repair(
        {
            "sheets": [
                {
                    "name": "A",
                    "columns": [{"header": "X", "type": "number"}],
                    "rows": [["1.5"], ["2"]],
                }
            ]
        }
    )

    rows = result["schema"]["sheets"][0]["rows"]
    assert rows[0][0] == 1.5
    assert isinstance(rows[1][0], int)


def test_does_not_coerce_strings_in_a_string_column() -> None:
    result = holy_sheet.validate_and_repair(
        {
            "sheets": [
                {
                    "name": "A",
                    "columns": [{"header": "Zip", "type": "string"}],
                    "rows": [["02134"]],
                }
            ]
        }
    )

    assert result["schema"]["sheets"][0]["rows"][0][0] == "02134"


def test_infers_a_date_column_type_from_iso_row_values() -> None:
    result = holy_sheet.validate_and_repair(
        {
            "sheets": [
                {
                    "name": "A",
                    "columns": [{"header": "When"}],
                    "rows": [["2024-01-01"], ["2024-02-01"]],
                }
            ]
        }
    )

    assert result["schema"]["sheets"][0]["columns"][0]["type"] == "date"
    assert any("inferred" in repair for repair in result["repairs"])


def test_infers_datetime_when_the_first_value_carries_a_time() -> None:
    result = holy_sheet.validate_and_repair(
        {
            "sheets": [
                {
                    "name": "A",
                    "columns": [{"header": "When"}],
                    "rows": [["2024-01-01T09:30:00Z"], ["2024-02-01T10:00:00Z"]],
                }
            ]
        }
    )

    assert result["schema"]["sheets"][0]["columns"][0]["type"] == "datetime"


def test_replaces_an_unknown_theme_with_default() -> None:
    result = holy_sheet.validate_and_repair(
        {"sheets": [{"name": "A", "theme": "wonkyland", "rows": []}]}
    )

    assert result["schema"]["sheets"][0]["theme"] == "default"


def test_trims_whitespace_from_sparse_cell_addresses() -> None:
    result = holy_sheet.validate_and_repair(
        {"sheets": [{"name": "A", "cells": {" A1 ": {"value": 1}}}]}
    )

    assert "A1" in result["schema"]["sheets"][0]["cells"]
    assert any("trimmed whitespace" in repair for repair in result["repairs"])


def test_passes_a_valid_schema_through_unchanged() -> None:
    valid = {"sheets": [{"name": "A", "rows": [["x"]]}]}
    result = holy_sheet.validate_and_repair(valid)

    assert result["schema"] == valid
    assert result["repairs"] == []


def test_does_not_invent_missing_required_fields() -> None:
    """A missing `name` stays missing -- the agent should see the error."""
    result = holy_sheet.validate_and_repair({"sheets": [{"rows": []}]})

    assert result["errors"] != []


def test_does_not_mutate_the_caller_s_schema() -> None:
    original = {"sheet": [{"name": "A", "rows": []}]}
    holy_sheet.validate_and_repair(original)

    assert original == {"sheet": [{"name": "A", "rows": []}]}
