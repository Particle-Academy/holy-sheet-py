"""Ported from `holy-sheet/tests/Unit/FormulaPromotionTest.php`.

This behaviour is PHP-only today -- the Node port is a minor behind and has
neither bare-scalar `cells` entries nor "="-promotion. PHP is the reference for
semantics, so the Python port implements PHP's.
"""

from __future__ import annotations

import holy_sheet
from holy_sheet import Normalizer


def _cells(schema: dict) -> dict:
    return Normalizer().normalize(schema).sheets[0].cells


def test_promotes_a_bare_equals_string_row_cell_to_a_formula() -> None:
    schema = {
        "sheets": [
            {
                "name": "T",
                "columns": [{"header": "A"}, {"header": "B"}, {"header": "C"}],
                "rows": [[10, 20, "=A2+B2"]],
            }
        ]
    }

    cell = _cells(schema)["C2"]

    assert cell.formula == "A2+B2"
    assert cell.value is None


def test_promotes_a_bare_equals_string_in_a_cells_map() -> None:
    cell = _cells({"sheets": [{"name": "T", "cells": {"A1": "=SUM(B1:B5)"}}]})["A1"]

    assert cell.formula == "SUM(B1:B5)"
    assert cell.value is None


def test_does_not_promote_an_object_cell_with_an_explicit_value() -> None:
    """`{"value": "=literal"}` is the escape hatch for a real leading-= string."""
    cell = _cells({"sheets": [{"name": "T", "cells": {"A1": {"value": "=literal"}}}]})["A1"]

    assert cell.formula is None
    assert cell.value == "=literal"


def test_leaves_an_explicit_formula_object_untouched() -> None:
    cell = _cells(
        {"sheets": [{"name": "T", "cells": {"A1": {"value": None, "formula": "SUM(B1:B5)"}}}]}
    )["A1"]

    assert cell.formula == "SUM(B1:B5)"


def test_does_not_promote_a_lone_equals_or_a_plain_string() -> None:
    cells = _cells({"sheets": [{"name": "T", "cells": {"A1": "=", "A2": "hello"}}]})

    assert cells["A1"].formula is None
    assert cells["A1"].value == "="
    assert cells["A2"].value == "hello"


def test_a_bare_scalar_cell_keeps_its_type() -> None:
    cells = _cells(
        {"sheets": [{"name": "T", "cells": {"A1": 42, "A2": True, "A3": "1e5"}}]}
    )

    assert cells["A1"].value == 42
    assert cells["A2"].value is True
    # PHP's numeric-string coercion still applies to a bare scalar.
    assert cells["A3"].value == 100000.0


def test_a_promoted_formula_lints_but_a_literal_does_not() -> None:
    promoted = {"sheets": [{"name": "T", "cells": {"A1": "=10/0"}}]}
    literal = {"sheets": [{"name": "T", "cells": {"A1": {"value": "=10/0"}}}]}

    assert holy_sheet.lint(promoted) != []  # evaluated -> an Excel error
    assert holy_sheet.lint(literal) == []  # stored as text -> nothing to lint


def test_a_promoted_formula_schema_writes_valid_bytes() -> None:
    schema = {
        "sheets": [
            {
                "name": "T",
                "columns": [{"header": "A"}, {"header": "B"}, {"header": "C"}],
                "rows": [[10, 20, "=A2+B2"]],
            }
        ]
    }

    assert holy_sheet.to_bytes(schema)[:4] == b"PK\x03\x04"
