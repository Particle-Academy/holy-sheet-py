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


# --------------------------------------------------------------------------- #
# Excel semantics (fancy-formula plan, decision D17). Ported from the PHP     #
# 2.11.1 tests, case for case, plus lazy IF.                                  #
#                                                                             #
# The linter only reports errors, so a value is observed through a division:  #
# `1/(<claim>)` lints clean exactly when the claim is TRUE (1/1), and as      #
# #DIV/0! when it is FALSE (1/0). Division rather than IF because it is the   #
# smaller claim: it does not depend on IF being lazy, which is tested on its  #
# own below. holy-sheet writes for Excel, so Excel decides.                   #
# --------------------------------------------------------------------------- #

# The tail of every syntax hint, byte for byte the PHP and JS text.
SYNTAX_TAIL = (
    ". Excel cannot parse this formula. Check for an unbalanced parenthesis or quote, "
    "a missing operator or argument, or a character a formula cannot contain. Text goes in "
    'double quotes, and a quote inside text is written twice: "a""b".'
)
SUPPORTED = (
    "Holy Sheet supports: SUM, AVERAGE, COUNT, COUNTA, MIN, MAX, IF, ROUND, ABS, LEN, "
    "UPPER, LOWER, CONCAT."
)


def _lint_formula(formula: str, cells: dict | None = None) -> list[dict[str, str]]:
    return holy_sheet.lint(
        {"sheets": [{"name": "S", "cells": {**(cells or {}), "Z1": {"formula": formula}}}]}
    )


def _lint_claim(claim: str, cells: dict | None = None) -> list[dict[str, str]]:
    return _lint_formula(f"1/({claim})", cells)


def _z1(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return [issue for issue in issues if issue["address"] == "Z1"]


def test_percent_is_postfix_not_a_binary_operator() -> None:
    assert _lint_claim("50%=0.5") == []
    assert _lint_claim("A1%=0.5", {"A1": {"value": 50}}) == []
    assert _lint_claim("-50%=-0.5") == []
    assert _lint_claim("200%*3=6") == []
    assert _lint_claim("(1+1)%=0.02") == []


def test_a_binary_percent_is_an_error_since_excel_rejects_it() -> None:
    # The old parser read 10%2 as 10/100*2 and passed it.
    issues = _lint_formula("10%2")
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"


def test_caret_binds_tighter_than_multiply_and_divide() -> None:
    assert _lint_claim("2*3^2=18") == []
    assert _lint_claim("10/2^2=2.5") == []
    assert _lint_claim("2^3*2=16") == []
    # Excel's ^ is left-associative, and negation binds tighter than ^.
    assert _lint_claim("2^3^2=64") == []
    assert _lint_claim("-2^2=4") == []
    assert _lint_claim("2^-1=0.5") == []
    # Percent binds tighter still: (50%)^2.
    assert _lint_claim("50%^2=0.25") == []


def test_a_doubled_quote_inside_text_is_one_quote() -> None:
    # ="a""b" is the three characters a"b.
    assert _lint_claim('LEN("a""b")=3') == []
    assert _lint_claim('"a""b"="a"&"""b"') == []
    assert _lint_claim('LEN("""")=1') == []
    assert _lint_claim('LEN("")=0') == []


def test_if_reads_true_and_false_text_in_any_case_as_excel_does() -> None:
    # The text "FALSE" WAS already falsy; that part matched Excel. What did not
    # was every other spelling: "false" counted as TRUE.
    assert _lint_claim('IF("FALSE",1,2)=2') == []
    assert _lint_claim('IF("false",1,2)=2') == []
    assert _lint_claim('IF("True",1,2)=1') == []
    assert _lint_claim("IF(A1,1,2)=2", {"A1": {"value": "FALSE"}}) == []
    # An empty cell is FALSE, a non-zero number TRUE.
    assert _lint_claim("IF(A1,1,2)=2") == []
    assert _lint_claim("IF(-3,1,2)=1") == []


def test_if_given_text_that_is_not_true_or_false_is_value() -> None:
    # The old linter called any other text TRUE and "" FALSE. Excel errors.
    for formula in ('IF("abc",1,2)', 'IF("",1,2)', "IF(A1,1,2)"):
        issues = _lint_formula(formula, {"A1": {"value": "yes"}})
        assert len(issues) == 1, formula
        assert issues[0]["error"] == "#VALUE!"


def test_an_error_in_the_if_condition_propagates_instead_of_choosing_a_branch() -> None:
    issues = _z1(_lint_formula("IF(A1,1,2)", {"A1": {"formula": "1/0"}}))
    assert len(issues) == 1
    assert issues[0]["error"] == "#DIV/0!"


def test_len_counts_utf16_code_units_as_excel_does() -> None:
    # Python's len() counts code points, so the emoji was 1. Excel's LEN, and
    # PHP's and JS's, count UTF-16 code units: outside the BMP that is 2.
    assert _lint_claim('LEN("é")=1') == []
    assert _lint_claim('LEN("naïve café")=10') == []
    assert _lint_claim("LEN(A1)=2", {"A1": {"value": "日本"}}) == []
    assert _lint_claim('LEN("😀")=2') == []
    assert _lint_claim('LEN("a😀b")=4') == []


def test_numeric_text_compares_as_text_which_excel_sorts_above_every_number() -> None:
    # ="10">9 is TRUE in Excel, but so is 10>9, so it cannot tell the two
    # readings apart. "5">9 can.
    assert _lint_claim('"5">9') == []
    assert _lint_claim('"10">9') == []
    assert _lint_claim('"10"<>10') == []
    assert _lint_claim('"2">"10"') == []
    assert _lint_claim("A1>99", {"A1": {"value": "10"}}) == []


def test_the_rest_compares_as_excel_does() -> None:
    # Text ignores case, logicals sort last, blanks match.
    assert _lint_claim('"a"="A"') == []
    assert _lint_claim('"abc"<"ABD"') == []
    assert _lint_claim("TRUE>1") == []
    assert _lint_claim('TRUE>"zzz"') == []
    assert _lint_claim("FALSE<TRUE") == []
    assert _lint_claim("A1=0") == []
    assert _lint_claim('A1=""') == []
    assert _lint_claim("A1=FALSE") == []


def test_text_is_ordered_by_code_point_as_php_and_js_order_it() -> None:
    assert _lint_claim('"😀">"～"') == []


def test_an_error_propagates_through_a_comparison() -> None:
    issues = _z1(_lint_formula("A1>0", {"A1": {"formula": "1/0"}}))
    assert len(issues) == 1
    assert issues[0]["error"] == "#DIV/0!"


def test_a_malformed_formula_is_a_syntax_error_never_an_unknown_function() -> None:
    # #NAME? stays the label. The hint used to be one sentence for every cause,
    # "references an unknown function or has a syntax error", which sent an
    # agent hunting for a function that was fine.
    malformed = ["SUM(1,2", "(1+2", "1 2", "1+", "1 # 2", "10%2", '"abc', "'S'+1", "SUM(1,,"]
    for formula in malformed:
        issues = _lint_formula(formula)
        assert len(issues) == 1, formula
        assert issues[0]["error"] == "#NAME?"
        assert issues[0]["hint"].startswith("Syntax error: "), formula
        assert "unknown function" not in issues[0]["hint"].lower()


def test_the_unknown_function_is_named_in_its_hint() -> None:
    issues = _lint_formula("VLOOKUP(1, A1:B2, 2)")
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"
    assert issues[0]["hint"].startswith("Unknown function VLOOKUP.")
    assert "SUM, AVERAGE" in issues[0]["hint"]


def test_an_unknown_function_is_reported_even_in_an_untaken_if_branch() -> None:
    # Excel resolves names before it evaluates. The old linter returned #NAME?
    # as a VALUE, and IF discarded it.
    issues = _lint_formula("IF(FALSE, BOGUSFN(1), 1)")
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"
    assert issues[0]["hint"].startswith("Unknown function BOGUSFN.")


def test_an_inherited_name_error_points_at_the_cell_it_came_from() -> None:
    issues = _z1(_lint_formula("A1+1", {"A1": {"formula": "BOGUSFN(1)"}}))
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"
    assert "A1" in issues[0]["hint"]
    assert "unknown function" not in issues[0]["hint"].lower()


def test_round_is_half_away_from_zero_on_the_decimal_value() -> None:
    # A GUARD, as in PHP: php_round already gives Excel's answer here.
    cases = [
        "ROUND(2.675,2)=2.68", "ROUND(-2.675,2)=-2.68", "ROUND(1.005,2)=1.01",
        "ROUND(0.285,2)=0.29", "ROUND(8.325,2)=8.33", "ROUND(2.5,0)=3",
        "ROUND(-2.5,0)=-3", "ROUND(-0.5,0)=-1", "ROUND(1250,-2)=1300",
        "ROUND(-1250,-2)=-1300", "ROUND(1.45,1)=1.5",
    ]
    for claim in cases:
        assert _lint_claim(claim) == [], claim


def test_a_text_literal_that_never_closes_is_reported() -> None:
    # The old tokenizer ran the literal to the end of the formula and passed it.
    issues = _lint_formula('"abc')
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"


def test_the_name_hints_are_byte_for_byte_the_php_and_js_text() -> None:
    cases = [
        ("SUM(1,2", "Syntax error: expected ')', but the formula ends" + SYNTAX_TAIL),
        ("1 2", "Syntax error: unexpected number 2 after a complete expression" + SYNTAX_TAIL),
        ("1+", "Syntax error: the formula ends where a value was expected" + SYNTAX_TAIL),
        ("1 # 2", "Syntax error: unexpected character '#'" + SYNTAX_TAIL),
        ("1 é 2", "Syntax error: unexpected non-ASCII character outside a text literal" + SYNTAX_TAIL),
        ('"abc', 'Syntax error: a text literal opens with " and never closes' + SYNTAX_TAIL),
        ("'S'+1", "Syntax error: the quoted name 'S' is not followed by ! and a cell reference" + SYNTAX_TAIL),
        ("SUM(1,,", "Syntax error: unexpected ',' where a value was expected" + SYNTAX_TAIL),
        ('"a""b" 3', "Syntax error: unexpected number 3 after a complete expression" + SYNTAX_TAIL),
        ("VLOOKUP(1, A2:B3, 2)", "Unknown function VLOOKUP. " + SUPPORTED),
        ("A1+1", "This formula depends on A1, whose value is #NAME?. Fix that cell first."),
    ]
    for formula, hint in cases:
        issues = _z1(_lint_formula(formula, {"A1": {"formula": "BOGUSFN(1)"}}))
        assert len(issues) == 1, formula
        assert issues[0]["hint"] == hint, formula


def test_the_tokenizer_uses_php_ascii_character_classes() -> None:
    # str.isspace / isalpha / isdigit are Unicode-aware, PHP's ctype_* are not:
    # a no-break space, "é" or a superscript two outside text is a syntax error
    # there, so here too. ("²" used to tokenize as a number and crash float().)
    for formula in ("1" + chr(160) + "+1", "é1+1", "2²"):
        issues = _lint_formula(formula)
        assert len(issues) == 1, formula
        assert issues[0]["hint"] == (
            "Syntax error: unexpected non-ASCII character outside a text literal" + SYNTAX_TAIL
        ), formula


# --------------------------------------------------------------------------- #
# IF is lazy, as in Excel: only the branch it takes is evaluated. The linter  #
# evaluates while it parses, so the untaken branch is still PARSED (a syntax  #
# error or an unknown function there is still reported) but never EVALUATED. #
# --------------------------------------------------------------------------- #


def test_if_does_not_evaluate_the_branch_it_does_not_take() -> None:
    # The guard every spreadsheet writes. Eager IF reported #DIV/0! on it.
    assert _lint_formula("IF(A2=0,0,1/A2)", {"A2": {"value": 0}}) == []
    assert _lint_formula("IF(TRUE,1,1/0)") == []
    assert _lint_formula("IF(FALSE,1/0,1)") == []
    assert _lint_formula("IF(1,2,1/0)+1") == []
    # An untaken branch reads no cells, so their text and errors stay out.
    assert _lint_formula("IF(TRUE,1,A1*2)", {"A1": {"value": "Annual"}}) == []
    assert _lint_formula("IF(FALSE,Nope!A1,1)") == []
    assert _lint_formula("IF(TRUE,1,foo)") == []
    assert _lint_formula("IF(TRUE,1,A1:foo)") == []


def test_if_still_evaluates_the_branch_it_takes() -> None:
    for formula in ("IF(TRUE,1/0,1)", "IF(FALSE,1,1/0)", "IF(A2=0,1/A2,0)"):
        issues = _lint_formula(formula, {"A2": {"value": 0}})
        assert len(issues) == 1, formula
        assert issues[0]["error"] == "#DIV/0!"


def test_an_untaken_if_branch_evaluates_nothing_nested_ifs_included() -> None:
    assert _lint_formula("IF(TRUE,1,IF(1/0,1/0,1/0))") == []
    assert _lint_formula("IF(FALSE,IF(1/0,1,2),3)") == []
    assert _lint_formula('IF(TRUE,1,SUM(1/0,"x"*2)&-("y")%)') == []
    assert _lint_formula("IF(TRUE,IF(FALSE,1/0,2),3)") == []


def test_if_without_an_else_branch_is_false() -> None:
    # =IF(FALSE,1) is FALSE in Excel, and FALSE is not an error.
    assert _lint_formula("IF(FALSE,1/0)") == []
    # Through &, where FALSE is "FALSE" and a blank is "": IF(...)=FALSE
    # cannot tell them apart, since a blank equals FALSE.
    assert _lint_claim('IF(FALSE,1)&""="FALSE"') == []


def test_a_syntax_error_in_an_untaken_if_branch_is_still_reported() -> None:
    for formula in ("IF(TRUE,1,2+)", "IF(FALSE,(1,2)", 'IF(TRUE,1,"abc)', "IF(TRUE,1,2 3)"):
        issues = _lint_formula(formula)
        assert len(issues) == 1, formula
        assert issues[0]["error"] == "#NAME?"
        assert issues[0]["hint"].startswith("Syntax error: "), formula


def test_the_whole_if_is_parsed_before_an_error_in_its_condition_propagates() -> None:
    # Excel refuses a formula it cannot parse, so the syntax error is the one
    # to report. (A computed error such as 1/0 still stops the parse where it
    # arises, in IF as anywhere else in a formula; this is the error the
    # condition evaluates to.)
    issues = _z1(_lint_formula("IF(A1,1,2+)", {"A1": {"formula": "1/0"}}))
    assert len(issues) == 1
    assert issues[0]["error"] == "#NAME?"
    assert issues[0]["hint"].startswith("Syntax error: ")


def test_if_with_fewer_than_two_arguments_is_still_value() -> None:
    for formula in ("IF(TRUE)", "IF()"):
        issues = _lint_formula(formula)
        assert len(issues) == 1, formula
        assert issues[0]["error"] == "#VALUE!"
