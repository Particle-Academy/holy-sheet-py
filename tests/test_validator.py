"""Ported from `holy-sheet/tests/Unit/ValidatorTest.php`."""

from __future__ import annotations

import pytest

import holy_sheet
from holy_sheet import SchemaException


def test_a_valid_schema_produces_no_errors() -> None:
    errors = holy_sheet.validate(
        {"sheets": [{"name": "OK", "columns": [{"header": "A"}], "rows": [[1]]}]}
    )
    assert errors == []


def test_flags_a_missing_top_level_sheets_key() -> None:
    errors = holy_sheet.validate({})
    assert len(errors) == 1
    assert errors[0]["path"] == "sheets"
    assert errors[0]["expected"] == "array"


def test_flags_an_empty_sheets_array() -> None:
    errors = holy_sheet.validate({"sheets": []})
    assert errors[0]["path"] == "sheets"
    assert "non-empty" in errors[0]["expected"]


def test_flags_a_sheet_missing_its_name() -> None:
    errors = holy_sheet.validate(
        {"sheets": [{"columns": [{"header": "A"}], "rows": [[1]]}]}
    )
    assert errors[0]["path"] == "sheets[0].name"


def test_flags_an_unknown_column_type() -> None:
    errors = holy_sheet.validate(
        {
            "sheets": [
                {"name": "X", "columns": [{"header": "A", "type": "banana"}], "rows": [[1]]}
            ]
        }
    )
    assert len(errors) == 1
    assert errors[0]["path"] == "sheets[0].columns[0].type"
    assert isinstance(errors[0]["hint"], str)


def test_flags_an_unknown_theme() -> None:
    errors = holy_sheet.validate(
        {
            "sheets": [
                {
                    "name": "X",
                    "columns": [{"header": "A"}],
                    "rows": [[1]],
                    "theme": "neon",
                }
            ]
        }
    )
    assert errors[0]["path"] == "sheets[0].theme"


def test_flags_a_sheet_with_neither_rows_nor_cells() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "X"}]})
    assert errors[0]["path"] == "sheets[0]"
    assert errors[0]["expected"] == "object with rows OR cells"


def test_flags_a_cells_value_that_is_a_list_rather_than_a_map() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "X", "cells": [1, 2, 3]}]})
    assert errors[0]["path"] == "sheets[0].cells"
    assert errors[0]["got"] == "array"


def test_write_raises_schema_exception_with_structured_errors(tmp_path) -> None:
    with pytest.raises(SchemaException) as caught:
        holy_sheet.write({}, str(tmp_path / "should-not-be-written.xlsx"))

    assert caught.value.get_errors()[0]["path"] == "sheets"
    assert not (tmp_path / "should-not-be-written.xlsx").exists()


def test_the_exception_message_summarises_the_first_error() -> None:
    try:
        holy_sheet.to_bytes({"sheets": [{"name": "", "rows": []}, {"name": "", "rows": []}]})
    except SchemaException as error:
        assert "schema invalid at sheets[0].name" in str(error)
        assert "+1 more" in str(error)
    else:  # pragma: no cover
        raise AssertionError("expected SchemaException")


def test_the_got_field_never_reports_a_bool_as_an_int() -> None:
    """Python's `isinstance(True, int)` would make `got` say "int"."""
    errors = holy_sheet.validate({"sheets": True})
    assert errors[0]["got"] == "bool"


# A sheet with no cells describes as `cells: []` in PHP (an empty PHP array), and
# that JSON is what reaches this validator. PHP read it as a list and rejected it,
# and so did this port, breaking describe() -> write() for an empty sheet.
def test_accepts_an_empty_cells_array_which_is_how_php_describes_an_empty_sheet() -> None:
    assert holy_sheet.validate({"sheets": [{"name": "Empty", "cells": []}]}) == []
    assert holy_sheet.validate({"sheets": [{"name": "Empty", "cells": {}}]}) == []


def test_still_flags_cells_given_as_a_non_empty_list() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "Bad", "cells": [{"value": 1}]}]})
    assert len(errors) == 1
    assert errors[0]["path"] == "sheets[0].cells"


def test_writes_back_a_described_workbook_that_has_an_empty_sheet() -> None:
    data = holy_sheet.to_bytes(
        {
            "sheets": [
                {"name": "Data", "columns": [{"header": "A"}], "rows": [[1]]},
                {"name": "Empty", "cells": []},
            ]
        }
    )
    described = holy_sheet.read(data)
    assert described["sheets"][1] == {"name": "Empty", "cells": {}}
    assert holy_sheet.read(holy_sheet.to_bytes(described)) == described

