"""Run this package against the shared `fancy-conformance` fixture tables.

`shared/decimal` names `particle-academy/holy-sheet` as its reference
implementation for two of its three functions, so this package is not merely
*checked* by that table — it is one of the implementations the table was taken
from. Every row in it was a live PHP-vs-JS disagreement in a shipped package.

- `formatFloat` — the `<v>` serialisation of a numeric cell, PHP's
  `rtrim(rtrim(number_format($v, 14, '.', ''), '0'), '.')`. Three of these rows
  pin the bug that wrote `1e300` into a spreadsheet as `1e3`.
- `numericStringToNumber` — coercion of a numeric string supplied by a host.
- `roundMoney` — `(int) round($v)`, half away from zero. Python's builtin
  `round()` is half-to-EVEN and would fail four of these rows; `php_round` is
  the reason it does not.

Four rules from `runners/README.md`, all honoured:

1. Run on every push and PR — not nightly, not at release.
2. A missing fixture checkout is a FAILURE, not a skip (the loader raises).
3. Print the summary unconditionally, including every skip and its reason.
4. Print and assert the pinned suite version.
"""

from __future__ import annotations

import pytest

from holy_sheet.helpers.php import format_float, numeric_string_to_number, php_round
from tests.conformance import loader

# Asserted, not merely printed: "we are on an old fixture set" should be visible
# in the log rather than inferred months later.
PINNED_SUITE_VERSION = "0.5.0"

DISPATCH = {
    "formatFloat": lambda v: format_float(float(v)),
    "numericStringToNumber": lambda v: numeric_string_to_number(str(v)),
    "roundMoney": lambda v: int(php_round(float(v))),
}


def test_the_pinned_fixture_version_is_the_one_on_disk() -> None:
    assert loader.version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {loader.version()}, this port pins "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def test_this_port_implements_every_function_the_decimal_suite_declares() -> None:
    """The guard the sibling parity suites lacked.

    If the suite gains a fourth function, or renames one, the runner below would
    quietly stop covering it and still report green. This turns that into a
    failure with the missing name in the message.
    """
    declared = {c["fn"] for c in loader.cases("shared/decimal")}

    assert declared == set(DISPATCH), (
        f"shared/decimal declares {sorted(declared)}; this runner dispatches "
        f"{sorted(DISPATCH)}. Every function in the table must be routed to real "
        "code, or the row it belongs to is asserting nothing."
    )


def test_matches_the_shared_decimal_table() -> None:
    summary = loader.run_table(
        "shared/decimal", lambda c: DISPATCH[c["fn"]](c["input"]["value"])
    )
    # Printed unconditionally. A bare "3 skipped" in a log reads identically to
    # full coverage at a glance, so every skip is named with its reason.
    print("\n" + loader.format_summary(summary))

    assert summary["passed"] >= 15, "the decimal table barely ran"
    assert summary["ok"], loader.format_summary(summary)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 1), (1.5, 2), (2.5, 3), (-0.5, -1), (-1.5, -2), (-2.5, -3)],
)
def test_php_round_is_half_away_from_zero_not_bankers(value: float, expected: int) -> None:
    """The single highest-frequency divergence risk in a Python port of this family.

    Python's builtin would answer 0, 2, 2, 0, -2, -2 here. Asserted directly as
    well as through the table, because this helper is called from the writer,
    the reader and the normalizer, and a regression in it moves output bytes
    everywhere at once.
    """
    assert int(php_round(value)) == expected
