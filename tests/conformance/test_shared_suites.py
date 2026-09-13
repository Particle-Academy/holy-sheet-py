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

import re
from pathlib import Path

import pytest

from holy_sheet.helpers.php import format_float, numeric_string_to_number, php_round
import fancy_conformance as loader

# Asserted, not merely printed: "we are on an old fixture set" should be visible
# in the log rather than inferred months later.
# Moved to 0.20.0 on 2026-09-10, deliberately and not to get to green: every
# table above was re-run against the checkout FIRST and every row passes, with
# the only skip being the documented cross-engine one. shared/decimal 18 rows.
#
# Five ports had drifted to a pin this stale at once, which says the failure is
# structural rather than anyone forgetting: the pin only moves when a human
# re-runs the tables, and nothing prompts that when the fixture package ships.
#
# CI checks out `ref: v<this>` from .github/workflows/ci.yml. Move the two
# together; test_ci_checks_out_the_fixture_tag_this_suite_pins fails otherwise.
PINNED_SUITE_VERSION = "0.22.0"

DISPATCH = {
    "formatFloat": lambda v: format_float(float(v)),
    "numericStringToNumber": lambda v: numeric_string_to_number(str(v)),
    "roundMoney": lambda v: int(php_round(float(v))),
}


def test_the_pinned_fixture_version_is_the_one_on_disk(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Past pytest's capture: a bare print() in a passing test never reaches the
    # CI log, which is the one place rules 3 and 4 of fancy-conformance's
    # runners/README.md need it.
    with capsys.disabled():
        print(f"\nfancy-conformance on disk: {loader.version()}, pinned: {PINNED_SUITE_VERSION}")

    assert loader.version() == PINNED_SUITE_VERSION, (
        f"fancy-conformance is at {loader.version()}, this port pins "
        f"{PINNED_SUITE_VERSION}. Re-run the suites and move the pin deliberately."
    )


def _conformance_checkout_refs(workflow: str) -> list[str | None]:
    """The `ref:` of every workflow step that checks out fancy-conformance.

    Plain text on purpose: a YAML parser would be a dependency for one assertion.
    A step is a `- ` line plus everything indented deeper than it. `None` is a
    step with no `ref`, which checks out whatever `main` is at that moment.
    """
    lines = workflow.splitlines()
    refs: list[str | None] = []
    for index, line in enumerate(lines):
        start = re.match(r"(\s*)- ", line)
        if not start:
            continue
        step = [line]
        for following in lines[index + 1 :]:
            body = following.strip()
            indent = len(following) - len(following.lstrip())
            if body and not body.startswith("#") and indent <= len(start.group(1)):
                break
            step.append(following)
        text = "\n".join(step)
        if re.search(
            r"^\s*(- )?repository:\s*[\"']?Particle-Academy/fancy-conformance[\"']?\s*(#.*)?$",
            text,
            re.MULTILINE,
        ):
            ref = re.search(r"^\s*(- )?ref:\s*[\"']?([^\"'\s#]+)", text, re.MULTILINE)
            refs.append(ref.group(2) if ref else None)
    return refs


def test_the_checkout_ref_parser_sees_a_missing_ref() -> None:
    workflow = """
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/fancy-conformance
          path: .fancy-conformance
      - name: Pinned
        uses: actions/checkout@v4
        with:
          repository: "Particle-Academy/fancy-conformance"
          ref: 'v1.2.3'  # a comment
      - uses: actions/checkout@v4
        with:
          repository: Particle-Academy/holy-sheet
          ref: v9.9.9
    """
    assert _conformance_checkout_refs(workflow) == [None, "v1.2.3"]


def test_ci_checks_out_the_fixture_tag_this_suite_pins() -> None:
    """The CI checkout `ref` and `PINNED_SUITE_VERSION` are one decision in two files.

    CI used to check fancy-conformance out with no `ref`, so every fixture release
    turned this build red at once for a reason no commit here caused, and it sat
    red for weeks. The pin is the contract: moving it is a deliberate commit in
    this repository, never a side effect of someone else's release.
    """
    here = Path(__file__).resolve()
    workflows = next(
        (p / ".github" / "workflows" for p in here.parents if (p / ".github/workflows").is_dir()),
        None,
    )
    assert workflows is not None, f"no .github/workflows above {here}"

    refs = {
        path.name: _conformance_checkout_refs(path.read_text(encoding="utf-8"))
        for path in sorted(workflows.glob("*.y*ml"))
    }
    refs = {name: found for name, found in refs.items() if found}
    # Vacuity guard: a parser that matched nothing would satisfy the loop below.
    assert refs, "no workflow checks out Particle-Academy/fancy-conformance"

    expected = f"v{PINNED_SUITE_VERSION}"
    for name, found in refs.items():
        assert found == [expected] * len(found), (
            f".github/workflows/{name} checks fancy-conformance out at {found}, but this "
            f"suite pins {PINNED_SUITE_VERSION}. Set `ref: {expected}` there, and move "
            "the pin and the ref together."
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


def test_matches_the_shared_decimal_table(capsys: pytest.CaptureFixture[str]) -> None:
    summary = loader.run_table(
        "shared/decimal", lambda c: DISPATCH[c["fn"]](c["input"]["value"])
    )
    # Printed unconditionally. A bare "3 skipped" in a log reads identically to
    # full coverage at a glance, so every skip is named with its reason.
    # Past pytest's capture: a bare print() in a passing test never reaches the
    # CI log, which is the one place rules 3 and 4 of fancy-conformance's
    # runners/README.md need it.
    with capsys.disabled():
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
