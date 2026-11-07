"""Data validation, ported case for case from PHP's DataValidationTest.

The error paths, ``expected`` texts and hints are PHP's, byte for byte; the
worksheet bytes are pinned against PHP's writer by the ``dataValidations``
fixture in ``tests/fixtures.py``, which ``test_parity_php.py`` runs.
"""

from __future__ import annotations

import copy
import io
import zipfile
from typing import Any

import pytest

import holy_sheet
from tests.fixtures import SCHEMAS

EXPECTED = (
    '<dataValidations count="9">'
    '<dataValidation type="list" allowBlank="1" showInputMessage="1" showErrorMessage="1" errorTitle="Unknown category" error="Choose a category from the list." promptTitle="Category" prompt="Pick a category." sqref="B2:B3"><formula1>&apos;Lists&apos;!$A$1:$A$3</formula1></dataValidation>'
    '<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="C2:C3"><formula1>&quot;Draft,Live,Retired &quot;&quot;old&quot;&quot;&quot;</formula1></dataValidation>'
    '<dataValidation type="whole" allowBlank="1" showErrorMessage="1" sqref="D2:D3"><formula1>0</formula1><formula2>1000</formula2></dataValidation>'
    '<dataValidation type="decimal" errorStyle="warning" operator="greaterThanOrEqual" showErrorMessage="1" error="Below the minimum &amp; then some &lt;x&gt;" sqref="E2:E500"><formula1>0.5</formula1></dataValidation>'
    '<dataValidation type="date" errorStyle="information" operator="notBetween" allowBlank="1" showErrorMessage="1" sqref="F2:F500"><formula1>46023</formula1><formula2>46387</formula2></dataValidation>'
    '<dataValidation type="textLength" operator="lessThanOrEqual" allowBlank="1" showInputMessage="1" showErrorMessage="1" prompt="At most 40 characters." sqref="G2:G500"><formula1>40</formula1></dataValidation>'
    '<dataValidation type="list" allowBlank="1" showDropDown="1" showErrorMessage="1" sqref="H2:H500"><formula1>&quot;Yes,No,7&quot;</formula1></dataValidation>'
    '<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="I2:I500"><formula1>$A$20:$A$22</formula1></dataValidation>'
    '<dataValidation type="decimal" operator="lessThan" allowBlank="1" showErrorMessage="1" sqref="J2"><formula1>2.25</formula1></dataValidation>'
    "</dataValidations>"
)


def _fixture() -> dict[str, Any]:
    return copy.deepcopy(SCHEMAS["dataValidations"])


def _sheet_xml(schema: Any, n: int = 1) -> str:
    with zipfile.ZipFile(io.BytesIO(holy_sheet.to_bytes(schema))) as z:
        return z.read(f"xl/worksheets/sheet{n}.xml").decode("utf-8")


def _validate(validation: Any, where: str = "sheet") -> list[list[str]]:
    sheet: dict[str, Any] = {"name": "S", "columns": [{"header": "A"}], "rows": [["x"]]}
    if where == "sheet":
        sheet["dataValidations"] = [validation]
    else:
        sheet["columns"][0]["validation"] = validation
    schema = {"sheets": [sheet, {"name": "Lists", "cells": {"A1": "a"}}]}
    return [[e["path"], e["expected"]] for e in holy_sheet.validate(schema)]


P = "sheets[0].dataValidations[0]"
CHOICE = "a non-empty string with no comma, or a whole number"
TYPES = "one of: list, whole, decimal, date, textLength"
RANGE = 'a cell or range on this sheet, like "B2:B100"'

CASES: list[tuple[str, Any, list[list[str]], str]] = [
    ("not an object", ["list"], [[P, "object"]], "sheet"),
    ("no type", {"range": "A2"}, [[f"{P}.type", TYPES]], "sheet"),
    ("unknown type", {"range": "A2", "type": "dropdown", "values": ["a"]}, [[f"{P}.type", TYPES]], "sheet"),
    ("no range", {"type": "list", "values": ["a"]}, [[f"{P}.range", RANGE]], "sheet"),
    ("range on another sheet", {"range": "Lists!A2", "type": "list", "values": ["a"]}, [[f"{P}.range", RANGE]], "sheet"),
    ("range past the grid", {"range": "A2:XFE2", "type": "list", "values": ["a"]}, [[f"{P}.range", RANGE]], "sheet"),
    ("range on a column rule", {"range": "A2", "type": "list", "values": ["a"]}, [["sheets[0].columns[0].validation.range", "absent"]], "column"),
    ("both values and source", {"range": "A2", "type": "list", "values": ["a"], "source": "Lists!A1"}, [[P, "exactly one of values or source"]], "sheet"),
    ("neither values nor source", {"range": "A2", "type": "list"}, [[P, "exactly one of values or source"]], "sheet"),
    ("values not a list", {"range": "A2", "type": "list", "values": "a,b"}, [[f"{P}.values", "a non-empty list of choices"]], "sheet"),
    ("empty values", {"range": "A2", "type": "list", "values": []}, [[f"{P}.values", "a non-empty list of choices"]], "sheet"),
    ("a choice with a comma", {"range": "A2", "type": "list", "values": ["a", "b,c", "", 1.5, True]}, [
        [f"{P}.values[1]", CHOICE], [f"{P}.values[2]", CHOICE], [f"{P}.values[3]", CHOICE], [f"{P}.values[4]", CHOICE],
    ], "sheet"),
    ("source not a range", {"range": "A2", "type": "list", "source": "Lists"}, [[f"{P}.source", "a cell range like \"'Lists'!A1:A10\""]], "sheet"),
    ("source on an unknown sheet", {"range": "A2", "type": "list", "source": "'Lsts'!A1:A3"}, [[f"{P}.source", "a sheet in this workbook"]], "sheet"),
    ("operator on a list", {"range": "A2", "type": "list", "values": ["a"], "operator": "equal", "value": 1}, [
        [f"{P}.operator", "absent"], [f"{P}.value", "absent"],
    ], "sheet"),
    ("list keys on a number rule", {"range": "A2", "type": "whole", "value": 1, "operator": "equal", "values": ["a"], "source": "A1", "showDropdown": True}, [
        [f"{P}.values", "absent"], [f"{P}.source", "absent"], [f"{P}.showDropdown", "absent"],
    ], "sheet"),
    ("unknown operator", {"range": "A2", "type": "whole", "operator": "atLeast", "value": 1}, [
        [f"{P}.operator", "one of: between, notBetween, equal, notEqual, greaterThan, lessThan, greaterThanOrEqual, lessThanOrEqual"],
    ], "sheet"),
    ("between without bounds", {"range": "A2", "type": "whole"}, [[f"{P}.min", "a whole number"], [f"{P}.max", "a whole number"]], "sheet"),
    ("between with value", {"range": "A2", "type": "decimal", "min": 1, "max": 2, "value": 3}, [[f"{P}.value", "absent"]], "sheet"),
    ("one-value operator with bounds", {"range": "A2", "type": "decimal", "operator": "greaterThan", "min": 1, "max": 2}, [
        [f"{P}.value", "a number"], [f"{P}.min", "absent"], [f"{P}.max", "absent"],
    ], "sheet"),
    ("whole with a fraction", {"range": "A2", "type": "whole", "operator": "equal", "value": 1.5}, [[f"{P}.value", "a whole number"]], "sheet"),
    ("decimal with a string", {"range": "A2", "type": "decimal", "operator": "equal", "value": "1.5"}, [[f"{P}.value", "a number"]], "sheet"),
    ("decimal with a bool", {"range": "A2", "type": "decimal", "operator": "equal", "value": True}, [[f"{P}.value", "a number"]], "sheet"),
    ("whole with a bool", {"range": "A2", "type": "whole", "operator": "equal", "value": True}, [[f"{P}.value", "a whole number"]], "sheet"),
    ("date not ISO", {"range": "A2", "type": "date", "operator": "equal", "value": "31/12/2026"}, [[f"{P}.value", "a date as YYYY-MM-DD"]], "sheet"),
    ("date not a day", {"range": "A2", "type": "date", "operator": "equal", "value": "2026-02-30"}, [[f"{P}.value", "a date as YYYY-MM-DD"]], "sheet"),
    ("negative text length", {"range": "A2", "type": "textLength", "operator": "equal", "value": -1}, [[f"{P}.value", "a whole number of characters, 0 or more"]], "sheet"),
    ("min above max", {"range": "A2", "type": "date", "min": "2026-12-31", "max": "2026-01-01"}, [[f"{P}.max", "a value no less than min"]], "sheet"),
    ("flags not booleans", {"range": "A2", "type": "list", "values": ["a"], "allowBlank": 1, "showDropdown": "no"}, [
        [f"{P}.showDropdown", "boolean"], [f"{P}.allowBlank", "boolean"],
    ], "sheet"),
    ("bad prompt", {"range": "A2", "type": "list", "values": ["a"], "prompt": "pick"}, [[f"{P}.prompt", "object {title?, text?}"]], "sheet"),
    ("long prompt", {"range": "A2", "type": "list", "values": ["a"], "prompt": {"title": "t" * 33, "text": "p" * 256}}, [
        [f"{P}.prompt.title", "a string of at most 32 characters"], [f"{P}.prompt.text", "a string of at most 255 characters"],
    ], "sheet"),
    ("bad error", {"range": "A2", "type": "list", "values": ["a"], "error": {"style": "fatal", "title": 7, "text": "e" * 226}}, [
        [f"{P}.error.style", "one of: stop, warning, information"],
        [f"{P}.error.title", "a string of at most 32 characters"],
        [f"{P}.error.text", "a string of at most 225 characters"],
    ], "sheet"),
    ("column rule fine", {"type": "list", "values": ["a"]}, [], "column"),
]


def test_accepts_the_whole_fixture_without_an_error() -> None:
    assert holy_sheet.validate(_fixture()) == []


def test_writes_every_validation_column_rules_first_then_the_sheet_list() -> None:
    assert EXPECTED in _sheet_xml(_fixture())


def test_places_data_validations_after_merge_cells_and_before_page_setup() -> None:
    xml = _sheet_xml(_fixture())
    merge, dv, page = xml.find("</mergeCells>"), xml.find("<dataValidations "), xml.find("<pageSetup ")
    assert 0 < merge < dv < page


def test_writes_nothing_for_a_sheet_without_validations() -> None:
    schema = _fixture()
    del schema["sheets"][0]["dataValidations"]
    for column in schema["sheets"][0]["columns"]:
        column.pop("validation", None)
    assert "dataValidation" not in _sheet_xml(schema)


def test_writes_a_single_row_column_rule_as_one_cell() -> None:
    xml = _sheet_xml({"sheets": [{"name": "S", "columns": [{"header": "Status", "validation": {"type": "list", "values": ["a", "b"]}}], "rows": [["a"]]}]})
    assert 'sqref="A2"><formula1>&quot;a,b&quot;</formula1>' in xml


def test_quotes_a_sheet_name_with_an_apostrophe_in_a_source() -> None:
    xml = _sheet_xml({"sheets": [
        {"name": "S", "cells": {"A1": "x"}, "dataValidations": [{"range": "A2:A9", "type": "list", "source": "'Bob''s list'!$B$2:B4"}]},
        {"name": "Bob's list", "cells": {"B2": "y"}},
    ]})
    assert "<formula1>&apos;Bob&apos;&apos;s list&apos;!$B$2:$B$4</formula1>" in xml


def test_counts_an_inline_list_at_255_characters_commas_included() -> None:
    values = ["abcd"] * 51

    def at(v: list[str]) -> list[dict[str, Any]]:
        return holy_sheet.validate({"sheets": [{"name": "S", "cells": {}, "dataValidations": [{"range": "A1", "type": "list", "values": v}]}]})

    assert at(values) == []
    values[0] = "abcde"
    assert at(values) == []
    values[1] = "abcde"
    errors = at(values)
    assert len(errors) == 1
    assert errors[0]["path"] == "sheets[0].dataValidations[0].values"
    assert errors[0]["expected"] == "at most 255 characters, commas included"
    assert errors[0]["got"] == "256 characters"
    assert errors[0]["hint"] == (
        "Excel cannot store an inline list longer than 255 characters. "
        "Put the choices in cells and point source at them, like \"'Lists'!A1:A40\"."
    )


def test_counts_characters_not_bytes() -> None:
    v = "é" * 127
    assert holy_sheet.validate({"sheets": [{"name": "S", "cells": {}, "dataValidations": [{"range": "A1", "type": "list", "values": [v, v]}]}]}) == []


@pytest.mark.parametrize(("name", "validation", "expected", "where"), CASES, ids=[c[0] for c in CASES])
def test_reports_each_bad_validation_at_its_path_in_php_wording(name: str, validation: Any, expected: list[list[str]], where: str) -> None:
    assert _validate(validation, where) == expected


def test_refuses_a_column_rule_on_a_sheet_with_no_data_rows() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "Template", "columns": [{"header": "Category", "validation": {"type": "list", "values": ["a"]}}], "rows": []}]})
    assert len(errors) == 1
    assert errors[0]["path"] == "sheets[0].columns[0].validation"
    assert errors[0]["expected"] == "a sheet with data rows"
    assert errors[0]["hint"] == (
        "A column validation covers the column's data rows, and this sheet has none. "
        "For a template, put it in the sheet's dataValidations with a range, like \"B2:B500\"."
    )


def test_refuses_a_data_validations_that_is_not_a_list() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "S", "cells": {}, "dataValidations": {"range": "A1"}}]})
    assert [e["path"] for e in errors] == ["sheets[0].dataValidations"]
    assert errors[0]["expected"] == "a list of validations"


def test_gives_a_hint_that_names_the_comma_rule() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "S", "cells": {}, "dataValidations": [{"range": "A1", "type": "list", "values": ["a,b"]}]}]})
    assert errors[0]["hint"] == (
        'Each choice is text, like "Open", or a whole number. Excel separates inline choices with commas '
        "and cannot escape one, so a choice containing a comma must come from cells: use source."
    )


def test_repairs_only_the_case_of_a_type_operator_or_error_style() -> None:
    out = holy_sheet.validate_and_repair({"sheets": [{
        "name": "S",
        "columns": [{"header": "A", "validation": {"type": "List", "values": ["a"]}}],
        "rows": [["a"]],
        "dataValidations": [
            {"range": "B2", "type": "WHOLE", "operator": "greaterthan", "value": 1, "error": {"style": "Warning"}},
            {"range": "C2", "type": "dropdown", "values": ["a"]},
        ],
    }]})
    assert out["repairs"] == [
        "changed 'sheets[0].columns[0].validation.type' from 'List' to 'list'",
        "changed 'sheets[0].dataValidations[0].type' from 'WHOLE' to 'whole'",
        "changed 'sheets[0].dataValidations[0].operator' from 'greaterthan' to 'greaterThan'",
        "changed 'sheets[0].dataValidations[0].error.style' from 'Warning' to 'warning'",
    ]
    assert [e["path"] for e in out["errors"]] == ["sheets[0].dataValidations[1].type"]
    assert out["schema"]["sheets"][0]["dataValidations"][0]["operator"] == "greaterThan"


def test_repair_never_mutates_the_callers_schema() -> None:
    """The repairer copies the sheet shallowly; the rules below it must be copied too."""
    schema = {"sheets": [{
        "name": "S",
        "columns": [{"header": "A", "validation": {"type": "List", "values": ["a"], "error": {"style": "Stop"}}}],
        "rows": [["a"]],
        "dataValidations": [{"range": "B2", "type": "WHOLE", "operator": "greaterthan", "value": 1}],
    }]}
    before = copy.deepcopy(schema)
    holy_sheet.validate_and_repair(schema)
    assert schema == before


def test_does_not_repair_a_validation_that_is_already_right() -> None:
    out = holy_sheet.validate_and_repair(_fixture())
    assert out["repairs"] == []
    assert out["errors"] == []
