"""Ported from `holy-sheet/tests/Unit/FormulaLinterTest.php`."""

from __future__ import annotations

import holy_sheet


def test_a_workbook_with_valid_formulas_reports_nothing() -> None:
    schema = {
        "sheets": [
            {
                "name": "Q4",
                "rows": [
                    ["Region", "Revenue", "Doubled"],
                    ["NA", 100, {"formula": "B2*2"}],
                    ["EU", 200, {"formula": "B3*2"}],
                    ["Total", {"formula": "SUM(B2:B3)"}, {"formula": "SUM(C2:C3)"}],
                ],
            }
        ]
    }
    assert holy_sheet.lint(schema) == []


def test_catches_the_header_row_off_by_one_and_suggests_the_right_row() -> None:
    """The single most common formula bug an LLM writes."""
    schema = {
        "sheets": [
            {
                "name": "Q4",
                "rows": [
                    ["Region", "Annual", "Monthly"],
                    ["NA", 12000, {"formula": "B1*12"}],
                ],
            }
        ]
    }

    issues = holy_sheet.lint(schema)

    assert len(issues) == 1
    assert issues[0]["error"] == "#VALUE!"
    assert issues[0]["address"] == "C2"
    assert 'B1 = "Annual" (string)' in issues[0]["hint"]
    assert "Did you mean B2" in issues[0]["hint"]


def test_catches_division_by_zero() -> None:
    schema = {"sheets": [{"name": "D", "rows": [["x"], [0], [{"formula": "100/A2"}]]}]}
    assert holy_sheet.lint(schema)[0]["error"] == "#DIV/0!"


def test_propagates_an_error_through_a_dependent_formula() -> None:
    schema = {
        "sheets": [
            {
                "name": "C",
                "rows": [
                    ["x"],
                    [{"formula": "A1+1"}],  # arithmetic on the header text
                    [{"formula": "A2"}],  # inherits the error
                ],
            }
        ]
    }
    assert len(holy_sheet.lint(schema)) > 0


def test_detects_a_true_circular_dependency() -> None:
    schema = {
        "sheets": [
            {"name": "C", "cells": {"A1": {"formula": "B1"}, "B1": {"formula": "A1"}}}
        ]
    }

    issues = holy_sheet.lint(schema)

    assert issues != []
    assert issues[0]["error"] == "#CIRC!"


def test_flags_unknown_function_names_as_name_error() -> None:
    schema = {"sheets": [{"name": "F", "rows": [["x"], [{"formula": "BOGUSFN(1,2)"}]]}]}
    assert holy_sheet.lint(schema)[0]["error"] == "#NAME?"


def test_evaluates_cross_sheet_references() -> None:
    schema = {
        "sheets": [
            {"name": "Detail", "rows": [["x"], [100], [200]]},
            {
                "name": "Summary",
                "cells": {"A1": {"value": "Total"}, "B1": {"formula": "SUM(Detail!A2:A3)"}},
            },
        ]
    }
    assert holy_sheet.lint(schema) == []


def test_handles_sum_and_average_over_a_numeric_column() -> None:
    schema = {
        "sheets": [
            {
                "name": "S",
                "rows": [
                    ["x"],
                    [10],
                    [20],
                    [30],
                    [{"formula": "SUM(A2:A4)"}],
                    [{"formula": "AVERAGE(A2:A4)"}],
                ],
            }
        ]
    }
    assert holy_sheet.lint(schema) == []


def test_catches_arithmetic_on_a_string_mid_expression() -> None:
    schema = {
        "sheets": [
            {
                "name": "M",
                "rows": [
                    ["x", "y"],
                    [10, "oops"],
                    [20, 30],
                    [{"formula": "A2+B2"}],
                ],
            }
        ]
    }

    issues = holy_sheet.lint(schema)

    assert issues[0]["error"] == "#VALUE!"
    assert 'B2 = "oops"' in issues[0]["hint"]


def test_reports_a_malformed_formula_rather_than_a_partial_parse() -> None:
    """A trailing token means the formula was malformed.

    Without the check, "SUM(A1:A2))" parses the valid prefix and reports a
    plausible number for a formula Excel will reject.
    """
    schema = {"sheets": [{"name": "M", "cells": {"A1": {"formula": "SUM(A2:A3))"}}}]}
    assert holy_sheet.lint(schema)[0]["error"] == "#NAME?"


def test_round_uses_half_away_from_zero() -> None:
    """ROUND(2.5, 0) is 3, not 2.

    Python's builtin `round` is banker's rounding and would make this 2 -- a
    formula the linter evaluates differently from the spreadsheet that will run
    it.
    """
    schema = {
        "sheets": [
            {
                "name": "R",
                "cells": {"A1": {"formula": "ROUND(2.5,0)/3"}, "A2": {"formula": "1/ROUND(0.4,0)"}},
            }
        ]
    }

    issues = holy_sheet.lint(schema)

    # ROUND(2.5,0) = 3 so A1 is fine; ROUND(0.4,0) = 0 so A2 divides by zero.
    assert [issue["address"] for issue in issues] == ["A2"]
    assert issues[0]["error"] == "#DIV/0!"


def test_treats_an_undefined_cell_as_empty_rather_than_an_error() -> None:
    schema = {"sheets": [{"name": "E", "cells": {"A1": {"formula": "Z99+1"}}}]}
    assert holy_sheet.lint(schema) == []


def test_evaluates_string_concatenation_and_comparisons() -> None:
    schema = {
        "sheets": [
            {
                "name": "S",
                "cells": {
                    "A1": {"value": "a"},
                    "A2": {"formula": 'A1&"b"'},
                    "A3": {"formula": "IF(1>0,10,20)"},
                    "A4": {"formula": 'CONCAT("x","y")'},
                },
            }
        ]
    }
    assert holy_sheet.lint(schema) == []


def test_reports_every_broken_formula_not_just_the_first() -> None:
    schema = {
        "sheets": [
            {
                "name": "M",
                "cells": {
                    "A1": {"value": "text"},
                    "B1": {"formula": "A1*2"},
                    "B2": {"formula": "NOPE()"},
                    "B3": {"formula": "1/0"},
                },
            }
        ]
    }

    errors = {issue["error"] for issue in holy_sheet.lint(schema)}
    assert errors == {"#VALUE!", "#NAME?", "#DIV/0!"}


# Sheet names that need quoting -- holy-sheet issue #6, ported case for case.
#
# Excel quotes any sheet name that is not a bare identifier ('My Sheet'!A1) and
# escapes a literal quote by doubling it ('Q3 ''Final'''!B2). The tokenizer had
# no case for the quote, so every such reference linted as #NAME?.


def _two_sheets(first: str, formula: str) -> dict:
    return {
        "sheets": [
            {"name": first, "columns": [{"header": "Deal Size", "type": "number"}], "rows": [[9407], [9750]]},
            {"name": "Summary", "columns": [{"header": "Total", "type": "number"}], "rows": [[{"formula": formula}]]},
        ]
    }


def test_a_quoted_sheet_name_lints_like_an_unquoted_one() -> None:
    assert holy_sheet.lint(_two_sheets("My Earnings Projection", "SUM('My Earnings Projection'!A2:A3)")) == []


def test_a_quoted_sheet_reference_resolves_to_the_real_cell() -> None:
    # A1 is the header text, so only a real lookup yields #VALUE!.
    issues = holy_sheet.lint(_two_sheets("My Earnings Projection", "'My Earnings Projection'!A1*2"))

    assert len(issues) == 1
    assert issues[0]["error"] == "#VALUE!"
    assert 'A1 = "Deal Size" (string)' in issues[0]["hint"]
    assert "Did you mean A2" in issues[0]["hint"]


def test_a_doubled_quote_is_one_literal_quote_in_a_sheet_name() -> None:
    assert holy_sheet.lint(_two_sheets("Q3 'Final'", "SUM('Q3 ''Final'''!A2:A3)")) == []


def test_a_missing_quoted_sheet_is_ref_and_the_hint_names_it() -> None:
    issues = holy_sheet.lint(_two_sheets("Deals", "SUM('No Such Sheet'!A2:A3)"))

    assert len(issues) == 1
    assert issues[0]["error"] == "#REF!"
    # Byte-for-byte the PHP and Node hint: an agent reads it, and the three
    # engines must say the same thing.
    assert issues[0]["hint"] == (
        "The formula refers to a sheet named 'No Such Sheet', and this workbook has no such "
        "sheet. Its sheets are: Deals, Summary. Quote a name that contains spaces or "
        "punctuation: 'My Sheet'!A1."
    )


def test_a_missing_unquoted_sheet_is_ref() -> None:
    issues = holy_sheet.lint(_two_sheets("Deals", "SUM(Nope!A2:A3)"))

    assert len(issues) == 1
    assert issues[0]["error"] == "#REF!"


def test_sheet_names_match_case_insensitively_as_excel_does() -> None:
    assert holy_sheet.lint(_two_sheets("Deals", "SUM(deals!A2:A3)")) == []
    assert holy_sheet.lint(_two_sheets("My Deals", "SUM('MY DEALS'!A2:A3)")) == []

    issues = holy_sheet.lint(_two_sheets("Deals", "DEALS!A1*2"))
    assert len(issues) == 1
    assert issues[0]["error"] == "#VALUE!"


def test_a_quote_that_never_closes_is_name() -> None:
    issues = holy_sheet.lint(_two_sheets("My Deals", "SUM('My Deals!A2:A3)"))

    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"


def test_a_quoted_name_that_is_not_a_sheet_reference_is_name() -> None:
    issues = holy_sheet.lint(_two_sheets("My Deals", "'My Deals'+1"))

    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"
