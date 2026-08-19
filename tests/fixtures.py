"""The shared fixture table the parity + determinism suites run over.

Ported from `holy-sheet-js/tests/parity.test.ts`, with two additions this port
needs and the Node table lacks -- see `numericHazards` and `wide` below.

Every fixture pins `meta` so `docProps/core.xml` is not a clock. Without it the
two runtimes stamp their own `gmdate()` and the part diff becomes a race
against the second boundary, which reads as a parity failure roughly once per
run and passes the rest of the time -- the worst possible test.
"""

from __future__ import annotations

from typing import Any

META = {"creator": "Parity", "created": "2024-01-01T00:00:00Z"}


def _wide_cells() -> dict[str, Any]:
    """30 columns of one row: A1 … Z1, AA1 … AD1.

    This exists to pin a shared WART, not a feature. Both shipped engines order
    the cells within a row lexicographically on the column letter -- `ksort` on
    a string key in PHP, `[...row.keys()].sort()` in Node -- so past column Z a
    row emits `A, AA, AB, AC, AD, B, C, …`. Excel tolerates it; it is neither
    Excel's canonical order nor anyone's intuition, and it is EXACTLY the kind
    of thing a third implementation quietly "fixes" on its way past, breaking
    parts parity for every wide sheet in every engine at once.

    The polyglot parity plan's ruling is explicit: replicate it, pin it with a
    30-column fixture, and let the fix be a coordinated release train rather
    than a port's unilateral decision.
    """
    letters = [chr(65 + i) for i in range(26)] + ["AA", "AB", "AC", "AD"]
    return {f"{letter}1": {"value": index} for index, letter in enumerate(letters)}


SCHEMAS: dict[str, Any] = {
    # The floor the parity suite checks itself against: if this one stops
    # producing parts, every other assertion in the file has quietly stopped
    # asserting too.
    "minimal": {
        "meta": META,
        "sheets": [{"name": "Sheet 1", "columns": [{"header": "A"}], "rows": [[1]]}],
    },
    # The numeric hazards, as an actual PARITY case rather than two per-engine
    # tables that happen to agree. Each row was a real shipped disagreement:
    #
    #   "1e5"    PHP 100000               JS 1        (parseInt stops at 'e')
    #   "2e-3"   PHP 0                    JS 0.002    (dot test -> (int))
    #   "1e21"   PHP 9223372036854775807  JS 1        (PHP_INT_MAX clamp)
    #   1e21     PHP 1000…0               JS "1e+21"  (not valid <v> content)
    #   1e300    PHP exact expansion      JS "1e+3"   (the trim ate the exponent)
    #
    # Python's own versions of the same traps are different and are covered
    # here too: `float()` accepts "inf"/"nan"/"1_000" where PHP's is_numeric
    # does not, `bool` is a subclass of `int`, and -- the one that needs a
    # fixture rather than an argument -- `f"{v:.14f}"` rounds half to EVEN
    # while PHP's `number_format` rounds half AWAY FROM ZERO. The last row
    # holds two exact ties at the 14th decimal (j/2**15 is the only shape of
    # double whose decimal expansion terminates in a 5 there), so a half-even
    # formatter fails this fixture instead of shipping.
    "numericHazards": {
        "meta": META,
        "sheets": [
            {
                "name": "Numbers",
                "rows": [
                    ["exponent strings", "1e5", "1E5", "1.5e3", "2e-3"],
                    ["magnitude past int range", "1e21", "99999999999999999999"],
                    ["large floats", 1e21, 1e300],
                    ["small + signed", 0.1, 1 / 3, -2.25, -0.0],
                    ["zero in optional positions", 0, "0", "007", ".5"],
                    ["ties at the 14th decimal", 3.0517578125e-05, 9.1552734375e-05],
                ],
            }
        ],
    },
    "sparse": {
        "meta": META,
        "sheets": [
            {
                "name": "Data",
                "cells": {
                    "A1": {"value": "Region"},
                    "A2": {"value": "North"},
                    "B1": {"value": "Revenue"},
                    "B2": {"value": 12000},
                    "B3": {"value": 9800.5},
                    "B4": {"formula": "SUM(B2:B3)"},
                    "C1": {"value": True},
                    "D1": {"value": "2024-03-15", "format": {"displayFormat": "date"}},
                },
            }
        ],
    },
    "rowOriented": {
        "meta": META,
        "sheets": [
            {
                "name": "Sales",
                "theme": "default",
                "columns": [
                    {"header": "Region", "type": "string"},
                    {"header": "Revenue", "type": "currency", "currency": "USD"},
                    {"header": "Margin", "type": "percent", "decimals": 1},
                ],
                "rows": [
                    ["North", 12000, 0.12],
                    ["South", 9800.5, 0.08],
                    ["East", 15000, 0.21],
                ],
                "totals": {"Revenue": "sum", "Margin": "avg"},
            }
        ],
    },
    "decorated": {
        "meta": META,
        "sheets": [
            {
                "name": "Decorated",
                "cells": {
                    "A1": {
                        "value": "Title",
                        "format": {
                            "bold": True,
                            "fontSize": 14,
                            "color": "#FF0000",
                            "textAlign": "center",
                        },
                    },
                    "A2": {
                        "value": "x",
                        "comment": {"text": "a note", "author": "Ada"},
                        "format": {"backgroundColor": "#FFFF00"},
                    },
                    "B2": {"value": "y", "format": {"borderBottom": "#000000"}},
                },
                "mergedRegions": [{"start": "A1", "end": "B1"}],
                "columnWidths": {0: 120, 1: 80},
                "frozenRows": 1,
                "frozenCols": 1,
            }
        ],
    },
    "multiSheet": {
        "meta": META,
        "sheets": [
            {"name": "One", "cells": {"A1": {"value": 1}}},
            {"name": "Two", "cells": {"A1": {"value": 2}, "A2": {"value": "two"}}},
        ],
    },
    "wide": {
        "meta": META,
        "sheets": [{"name": "Wide", "cells": _wide_cells()}],
    },
}
