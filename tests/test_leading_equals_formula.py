"""Ported from `holy-sheet/tests/Unit/LeadingEqualsFormulaTest.php`.

A cell's `formula` with a leading "=" is read the same way by every entry
point. `write()` always stripped the "=" while `lint()` parsed the raw text and
reported `#NAME?` "unexpected '='", so an agent following lint -> ops -> reduce
-> lint wrote "=A2*2", was told it was broken, and looped.
"""

from __future__ import annotations

from typing import Any

import holy_sheet


def _base() -> dict[str, Any]:
    return holy_sheet.validate_and_repair({
        "sheets": [{
            "name": "Model",
            "columns": [{"header": "Base", "type": "number"}, {"header": "Scenario", "type": "number"}],
            "rows": [[100, 0]],
        }],
        "meta": {"creator": "Test", "created": "2024-01-01T00:00:00Z"},
    })["schema"]


def _set(formula: str) -> dict[str, Any]:
    return holy_sheet.reduce(_base(), [{"type": "set_cell", "sheet": "Model", "address": "B2", "formula": formula}])


def test_lints_a_set_cell_formula_written_with_a_leading_equals_clean() -> None:
    assert holy_sheet.lint(_set("=A2*2")) == []
    assert holy_sheet.lint(_set("A2*2")) == []


def test_writes_the_equals_form_and_the_canonical_form_byte_for_byte_the_same() -> None:
    assert holy_sheet.to_bytes(_set("=A2*2")) == holy_sheet.to_bytes(_set("A2*2"))


def test_reads_a_repeated_leading_equals_the_way_write_does() -> None:
    assert holy_sheet.lint(_set("==A2*2")) == []
    assert holy_sheet.to_bytes(_set("==A2*2")) == holy_sheet.to_bytes(_set("A2*2"))


def test_agrees_in_validate_validate_and_repair_and_equivalent() -> None:
    a = _set("=A2*2")
    repaired = holy_sheet.validate_and_repair(a)
    assert holy_sheet.validate(a) == []
    assert repaired["errors"] == []
    assert holy_sheet.lint(repaired["schema"]) == []
    assert holy_sheet.equivalent(a, _set("A2*2")) is True


def test_lints_an_object_cell_formula_with_a_leading_equals_in_a_cells_map_clean() -> None:
    schema = {"sheets": [{"name": "S", "cells": {"A1": {"value": 2}, "B1": {"formula": "=A1*2"}, "C1": "==A1*3"}}]}
    assert holy_sheet.lint(schema) == []


def test_still_reports_a_real_error_in_an_equals_formula_with_the_canonical_text() -> None:
    issues = holy_sheet.lint(_set("=A2/0"))
    assert len(issues) == 1
    assert issues[0]["error"] == "#DIV/0!"
    assert issues[0]["formula"] == "A2/0"


def test_describes_the_canonical_formula_form_in_op_schema() -> None:
    set_cell = next(v for v in holy_sheet.op_schema()["oneOf"] if v["properties"]["type"]["const"] == "set_cell")
    formula = set_cell["properties"]["formula"]
    assert formula["type"] == "string"
    assert "without the leading '='" in formula.get("description", "")
    assert "a leading '=' is accepted and stripped" in formula.get("description", "")
