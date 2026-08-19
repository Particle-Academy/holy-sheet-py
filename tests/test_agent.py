"""Ported from `holy-sheet/tests/Unit/AgentTest.php` + `HolySheetTest.php`.

Also pins the SHAPE of the public surface, which is the one thing a port can get
wrong in a way no other test notices: a function named `toBytes` would work
perfectly and still be the wrong API for this language, and a `write()` that
returned a coroutine would break every caller written against the docs.
"""

from __future__ import annotations

import inspect

import holy_sheet
import holy_sheet.agent


def test_tool_definition_returns_the_parsed_json_schema() -> None:
    definition = holy_sheet.tool_definition()

    assert isinstance(definition, dict)
    assert "$schema" in definition
    assert definition["title"] == "Holy Sheet workbook schema"
    assert set(definition["definitions"]) >= {"Sheet", "Column", "CellData", "CellFormat"}


def test_describe_returns_not_found_for_a_missing_path() -> None:
    assert holy_sheet.describe("/this/path/does/not/exist.xlsx")["error"] == "not_found"


def test_to_bytes_returns_a_non_empty_xlsx() -> None:
    data = holy_sheet.to_bytes(
        {"sheets": [{"name": "X", "columns": [{"header": "A"}], "rows": [[1]]}]}
    )

    assert len(data) > 100
    assert data[:4] == b"PK\x03\x04"


def test_exposes_a_version() -> None:
    assert isinstance(holy_sheet.version(), str)
    assert holy_sheet.version() != ""


def test_records_the_php_feature_baseline_it_was_ported_from() -> None:
    """Three engines version independently; "which holy-sheet is this?" needs
    an answer that is not the package version."""
    assert holy_sheet.FEATURE_BASELINE == "1.3.0"


def test_the_agent_surface_is_module_level_snake_case() -> None:
    """PHP `Agent::toBytes` / TS `Agent.toBytes` -> `holy_sheet.to_bytes`."""
    expected = {
        "validate",
        "validate_and_repair",
        "to_bytes",
        "write",
        "read",
        "describe",
        "tool_definition",
        "from_array",
        "from_csv",
        "lint",
        "version",
    }

    for name in expected:
        assert callable(getattr(holy_sheet, name)), f"{name} missing from the façade"
        assert callable(getattr(holy_sheet.agent, name)), f"{name} missing from agent"


def test_write_is_synchronous() -> None:
    """PHP's is sync. Node's is async only because browsers have no sync FS,
    which is not a constraint Python shares."""
    assert not inspect.iscoroutinefunction(holy_sheet.write)
    assert not inspect.iscoroutinefunction(holy_sheet.read)


def test_the_low_level_classes_keep_their_peer_names() -> None:
    for name in (
        "Validator",
        "Repairer",
        "Normalizer",
        "FormulaLinter",
        "Inference",
        "Theme",
        "XlsxWriter",
        "XlsxReader",
        "ArrayBuilder",
        "CsvBuilder",
        "CellAddress",
        "SchemaException",
    ):
        assert hasattr(holy_sheet, name), f"{name} is not exported"


def test_the_input_schema_is_a_plain_dict_not_a_constructed_object() -> None:
    """Load-bearing: the declarative schema is something an agent emits in ONE
    shot, and the Validator is the gate. A dataclass would move the gate into a
    constructor and reject exactly the loose input `validate_and_repair` exists
    to fix."""
    loose = {"sheet": [{"name": "A", "row": [["x"]], "theme": "neon"}]}

    repaired = holy_sheet.validate_and_repair(loose)

    assert repaired["errors"] == []
    assert holy_sheet.to_bytes(repaired["schema"])[:2] == b"PK"


def test_write_reports_the_bytes_it_wrote(tmp_path) -> None:
    path = tmp_path / "out.xlsx"
    result = holy_sheet.write(
        {"sheets": [{"name": "A", "cells": {"A1": {"value": 1}}}]}, str(path)
    )

    assert result == {"path": str(path), "bytes": path.stat().st_size, "sheets": 1}
