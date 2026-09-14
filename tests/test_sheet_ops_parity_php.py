"""Cross-runtime OPS parity: `diff`, `reduce`, `equivalent`, `op_schema` and
`SheetDiff.hunks` give PHP's answer, call for call.

PHP holy-sheet 2.3.1 is the reference (2.3.0's ops, 2.3.1's op schema). Every
case is sent to PHP in ONE batch through `scripts/php_ops.php`, and each result
is compared with this port's as the JSON PHP would print for it
(`php_json_view`): op order, op key order, int versus float, and PHP's
list-or-object choice all have to match. A version
history written by one runtime is replayed by the other, so "the same ops" is
the contract, not "ops that also work".

The cases: every edit in `test_sheet_ops.EDITS` in both directions, each also
arriving as JSON (string `columnWidths` keys); the special cases the algorithm
branches on; a seeded run of random op lists, which are diffed AND reduced; the
reducer's PHP quirks; `equivalent` pairs; the op schema; and seeded `hunks`.
"""

from __future__ import annotations

import copy
import json
import random
import re
from typing import Any, Callable

import pytest

import holy_sheet
from holy_sheet import SheetDiff
from holy_sheet.ops._php_array import php_json_view
from tests import _oracle
from tests.test_sheet_ops import EDITS, edited, workbook

pytestmark = pytest.mark.parity

# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------


def _as_json(value: Any) -> Any:
    """What PHP receives: the same value, through JSON."""
    return json.loads(json.dumps(value))


def _named_sheets() -> dict[str, Any]:
    return {"sheets": [
        {"name": "S1", "cells": {"A1": {"value": "one"}}},
        {"name": "S2", "cells": {"A1": {"value": "two"}}},
        {"name": "S3", "cells": {"A1": {"value": "three"}}},
        {"name": "S4", "cells": {"A1": {"value": "four"}}},
    ]}


def _special_diffs() -> dict[str, tuple[Any, Any]]:
    cases: dict[str, tuple[Any, Any]] = {}

    cases["duplicate sheet names"] = (
        {"sheets": [{"name": "Same", "cells": {"A1": {"value": 1}}}, {"name": "Same", "cells": {"A1": {"value": 2}}}]},
        {"sheets": [{"name": "Same", "cells": {"A1": {"value": 3}}}]},
    )

    b = _named_sheets()
    b["sheets"] = [
        {"name": "Z", "cells": {"A1": {"value": "new"}}},
        {"name": "X", "cells": {"A1": {"value": "one"}}},  # S1 renamed, unchanged
        {"name": "S4", "cells": {"A1": {"value": "four"}, "B2": {"value": 4}}},
        {"name": "Y", "cells": {"A1": {"value": "two"}}},  # S2 renamed, unchanged
    ]
    cases["renames by content, a removal and an addition"] = (_named_sheets(), b)

    b = _named_sheets()
    b["sheets"][2] = {"name": "T3", "cells": {"A1": {"value": "three, edited"}}}
    cases["one gone and one arrived is a rename with edits"] = (_named_sheets(), b)

    b = _named_sheets()
    b["sheets"] = [b["sheets"][3], b["sheets"][1], b["sheets"][0], b["sheets"][2]]
    cases["sheets permuted"] = (_named_sheets(), b)

    b = workbook()
    b["sheets"][0]["frozenRows"] = 2.0
    cases["typed frozen panes"] = (workbook(), b)

    b = workbook()
    b["sheets"][0]["cells"]["C9"] = {"value": None}
    cases["a null cell"] = (workbook(), b)

    b = workbook()
    del b["meta"]
    cases["meta removed"] = (workbook(), b)

    # Python's True == 1: the case a bool-blind comparison would call unchanged.
    a = workbook()
    a["sheets"][0]["cells"]["B3"] = {"value": 1}
    b = workbook()
    b["sheets"][0]["cells"]["B3"] = {"value": True}
    cases["1 becomes true"] = (a, b)
    a = workbook()
    a["sheets"][0]["cells"]["C3"] = {"value": 0}
    b = workbook()
    b["sheets"][0]["cells"]["C3"] = {"value": False}
    cases["0 becomes false"] = (a, b)

    b = workbook()
    b["sheets"][0]["cells"]["B4"] = {"value": 1410000.0}
    cases["an int becomes a float"] = (workbook(), b)

    b = workbook()
    b["sheets"][0]["cells"]["B2"]["format"] = {}
    cases["an empty format"] = (workbook(), b)

    b = workbook()
    b["sheets"][0]["cells"]["B5"] = {"value": None, "formula": "SUM(B2:B4)", "comment": None}
    cases["a null comment"] = (workbook(), b)

    wide = {"sheets": [{"name": "W", "cells": {f"{chr(65 + c)}{r}": {"value": r * 10 + c} for r in range(1, 30) for c in range(6)}}]}
    shifted = copy.deepcopy(wide)
    shifted["sheets"][0]["cells"] = {
        (f"{a[0]}{int(a[1:]) + 2}" if int(a[1:]) >= 12 else a): cell for a, cell in wide["sheets"][0]["cells"].items()
    }
    for c in range(6):
        shifted["sheets"][0]["cells"][f"{chr(65 + c)}12"] = {"value": "new"}
    del shifted["sheets"][0]["cells"]["C5"]
    cases["two rows inserted into a wide sheet"] = (wide, shifted)

    authored = {"sheets": [{
        "name": "Deals",
        "columns": [{"header": "Name"}, {"header": "Value", "type": "currency"}],
        "rows": [["Acme", 1200], ["Globex", 800]],
        "totals": {"Value": "sum"},
    }]}
    read_back = holy_sheet.read(holy_sheet.to_bytes(authored))
    cases["an authored schema and its read-back"] = (authored, read_back)
    edited_back = copy.deepcopy(read_back)
    edited_back["sheets"][0]["cells"]["A2"] = {"value": "Initech"}
    cases["an authored schema and an edited read-back"] = (authored, edited_back)

    return cases


def _random_ops(rng: random.Random, schema: dict[str, Any], run: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A random op list that keeps the schema valid, and where it leads."""
    current = schema
    ops: list[dict[str, Any]] = []

    for step in range(rng.randint(1, 7)):
        names = [sheet["name"] for sheet in current["sheets"]]
        sheet = rng.choice(names)
        address = chr(65 + rng.randint(0, 4)) + str(rng.randint(1, 9))
        kind = rng.randrange(18)

        if kind == 0:
            op = {"type": "set_cell", "sheet": sheet, "address": address, "value": rng.randint(-5, 999)}
        elif kind == 1:
            op = {"type": "set_cell", "sheet": sheet, "address": address, "value": "t" + str(rng.randint(1, 9)), "format": {"italic": True}}
        elif kind == 2:
            op = {"type": "set_cell", "sheet": sheet, "address": address, "value": None, "formula": f"SUM(B2:B{rng.randint(3, 6)})"}
        elif kind == 3:
            op = {"type": "set_cell", "sheet": sheet, "address": address, "value": rng.random() * 100, "comment": {"text": "c", "author": "r"}}
        elif kind == 4:
            op = {"type": "clear_cell", "sheet": sheet, "address": address}
        elif kind == 5:
            op = {"type": "insert_rows", "sheet": sheet, "at": rng.randint(1, 9), "count": rng.randint(1, 3)}
        elif kind == 6:
            op = {"type": "delete_rows", "sheet": sheet, "at": rng.randint(1, 9), "count": rng.randint(1, 3)}
        elif kind == 7:
            op = {"type": "insert_columns", "sheet": sheet, "at": rng.randint(1, 5), "count": rng.randint(1, 2)}
        elif kind == 8:
            op = {"type": "delete_columns", "sheet": sheet, "at": rng.randint(1, 5), "count": rng.randint(1, 2)}
        elif kind == 9:
            op = {"type": "rename_sheet", "sheet": sheet, "name": f"R{run}-{step}"}
        elif kind == 10:
            op = {"type": "add_sheet", "index": rng.randint(0, len(names)), "sheet": {"name": f"N{run}-{step}", "cells": {"A1": {"value": step}}}}
        elif kind == 11 and len(names) > 1:
            op = {"type": "remove_sheet", "sheet": sheet}
        elif kind == 12:
            op = {"type": "move_sheet", "sheet": sheet, "toIndex": rng.randint(0, len(names) - 1)}
        elif kind == 13:
            op = {"type": "set_frozen", "sheet": sheet, "rows": rng.randint(0, 2), "cols": rng.randint(0, 2)}
        elif kind == 14:
            r = rng.randint(1, 8)
            op = {"type": "set_merged_regions", "sheet": sheet, "mergedRegions": [{"start": f"C{r}", "end": f"D{r + 1}"}]}
        elif kind == 15:
            op = {"type": "set_column_widths", "sheet": sheet, "columnWidths": {str(rng.randint(0, 4)): rng.randint(40, 200)}}
        elif kind == 16:
            op = {"type": "set_range", "sheet": sheet, "start": address, "values": [[rng.randint(0, 9), "r"], [None, 2.5]]}
        else:
            op = {"type": "set_meta", "meta": {"creator": f"run {run}", "created": "2026-09-15T00:00:00Z"}}

        candidate = holy_sheet.reduce(current, op)
        if holy_sheet.validate(candidate) == []:
            ops.append(op)
            current = candidate

    return current, ops


#: Reducer inputs where PHP's semantics are easy to miss. Each is (schema, ops).
QUIRKS: dict[str, tuple[Any, Any]] = {
    "type true is remove_sheet": ({"sheets": [{"name": "A", "cells": {}}, {"name": "B"}]}, {"type": True, "sheet": "B"}),
    "type 1 matches nothing": ({"sheets": [{"name": "A", "cells": {}}, {"name": "B"}]}, {"type": 1, "sheet": "B"}),
    "a padded address": ({"sheets": [{"name": "A", "cells": {}}]}, {"type": "set_cell", "sheet": "A", "address": " a1 ", "value": 1}),
    "cast column width keys": (
        {"sheets": [{"name": "S", "columnWidths": {"abc": 5, "3": 7, "1.5": 9}, "cells": {"B2": {"value": 1}}}]},
        {"type": "insert_columns", "sheet": "S", "at": 1, "count": 1},
    ),
    "string and float positions": (
        workbook(),
        [
            {"type": "insert_rows", "sheet": "Q3", "at": "2", "count": 1.9},
            {"type": "move_sheet", "sheet": "Notes", "toIndex": "0"},
            {"type": "set_frozen", "sheet": "Q3", "rows": "3x", "cols": 1.5},
            {"type": "add_sheet", "index": "1e0", "sheet": {"name": "Mid"}},
        ],
    ),
    "set_cell parts": (
        workbook(),
        [
            {"type": "set_cell", "sheet": "Q3", "address": "A1", "value": "Area"},
            {"type": "set_cell", "sheet": "Q3", "address": "B2", "format": None},
            {"type": "set_cell", "sheet": "Q3", "address": "B5", "formula": "SUM(B2:B3)", "computedValue": 7},
            {"type": "set_cell", "sheet": "Notes", "address": "A1", "value": "x", "comment": None},
            {"type": "set_cell", "sheet": "Q3", "address": "A2"},
            {"type": "set_cell", "sheet": "Q3", "address": "Z99", "value": None, "format": None},
            {"type": "set_cell", "sheet": "Q3", "address": "Z98", "format": {}},
        ],
    ),
    "set_range from a map": (
        workbook(),
        {"type": "set_range", "sheet": "Q3", "start": "c2", "end": "A1", "values": {"x": [1, None], "y": "not a row", "z": {"p": 3}}},
    ),
    "clear_cell forms": (
        {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}, "B2": {"value": 2}}}, {"name": "T"}]},
        [{"type": "clear_cell", "sheet": "S", "address": "b2"}, {"type": "clear_cell", "sheet": "T", "address": "A1"}],
    ),
    "clear_cell on a scalar cells": ({"sheets": [{"name": "S", "cells": 5}]}, {"type": "clear_cell", "sheet": "S", "address": "A1"}),
    "merges cut and dropped": (
        {"sheets": [{"name": "S", "mergedRegions": [{"start": "A2", "end": "C5"}, {"start": "B3", "end": "B3"}, {"start": "bad", "end": "B1"}, "junk"]}]},
        [{"type": "delete_rows", "sheet": "S", "at": 3, "count": 2}, {"type": "delete_columns", "sheet": "S", "at": 2, "count": 1}],
    ),
    "empties remove keys": (
        workbook(),
        [
            {"type": "set_merged_regions", "sheet": "Q3", "mergedRegions": []},
            {"type": "set_column_widths", "sheet": "Q3", "columnWidths": {}},
            {"type": "set_frozen", "sheet": "Q3", "rows": 0},
            {"type": "set_meta", "meta": None},
        ],
    ),
    "unknown sheets and types": (
        workbook(),
        [{"type": "set_cell", "sheet": "Nope", "address": "A1", "value": 1}, {"type": "frobnicate", "sheet": "Q3"}, {"sheet": "Q3"}],
    ),
    "set_workbook then more": (workbook(), [{"type": "set_workbook", "data": {"sheets": []}}, {"type": "add_sheet", "sheet": {"name": "Only"}}]),
    "an empty op list": (workbook(), []),
}


def _equivalent_pairs() -> dict[str, tuple[Any, Any]]:
    cases: dict[str, tuple[Any, Any]] = {}
    b = workbook()
    b["sheets"][0]["columnWidths"] = {0: 120.0, 1: 140.0}
    b["sheets"][0]["frozenCols"] = 0
    cases["float widths and a zero pane"] = (workbook(), b)
    b = workbook()
    b["sheets"][0]["cells"]["C9"] = {"value": None}
    cases["a null cell"] = (workbook(), b)
    b = workbook()
    b["meta"]["created"] = "2020-01-01T00:00:00Z"
    cases["two different created stamps"] = (workbook(), b)
    b = workbook()
    del b["meta"]["created"]
    cases["one created stamp"] = (workbook(), b)
    cases["an authored sheet and a cell sheet"] = (
        {"sheets": [{"name": "T", "columns": [{"header": "H"}], "rows": [["v"]]}]},
        {"sheets": [{"name": "T", "cells": {"A1": {"value": "H", "format": {"bold": True}}, "A2": {"value": "v"}}}]},
    )
    return cases


def _build_cases() -> list[tuple[str, dict[str, Any], Callable[[], Any]]]:
    """(id, the PHP call, the same call in Python)."""
    cases: list[tuple[str, dict[str, Any], Callable[[], Any]]] = []

    def diff_case(case_id: str, a: Any, b: Any) -> None:
        a_json, b_json = _as_json(a), _as_json(b)
        cases.append((
            f"diff: {case_id}",
            {"fn": "diff", "a": a_json, "b": b_json},
            lambda a_json=a_json, b_json=b_json, a=a, b=b: _both_inputs(
                lambda: {"ops": holy_sheet.diff(a_json, b_json)},
                lambda: {"ops": holy_sheet.diff(a, b)},
            ),
        ))

    for name in EDITS:
        diff_case(f"{name} (forward)", workbook(), edited(name))
        diff_case(f"{name} (reverse)", edited(name), workbook())

    for name, (a, b) in _special_diffs().items():
        diff_case(f"{name} (forward)", a, b)
        diff_case(f"{name} (reverse)", b, a)

    rng = random.Random(20260915)
    for run in range(30):
        start = workbook() if run % 2 == 0 else _named_sheets()
        target, ops = _random_ops(rng, start, run)
        diff_case(f"random run {run} (forward)", start, target)
        diff_case(f"random run {run} (reverse)", target, start)
        start_json, ops_json = _as_json(start), _as_json(ops)
        cases.append((
            f"reduce: random run {run}",
            {"fn": "reduce", "schema": start_json, "ops": ops_json},
            lambda s=start_json, o=ops_json: {"schema": holy_sheet.reduce(s, o)},
        ))

    for name, (schema, ops) in QUIRKS.items():
        schema_json, ops_json = _as_json(schema), _as_json(ops)
        cases.append((
            f"reduce: {name}",
            {"fn": "reduce", "schema": schema_json, "ops": ops_json},
            lambda s=schema_json, o=ops_json: {"schema": holy_sheet.reduce(s, o)},
        ))

    for name, (a, b) in _equivalent_pairs().items():
        a_json, b_json = _as_json(a), _as_json(b)
        cases.append((
            f"equivalent: {name}",
            {"fn": "equivalent", "a": a_json, "b": b_json},
            lambda a_json=a_json, b_json=b_json: {"equivalent": holy_sheet.equivalent(a_json, b_json)},
        ))

    cases.append(("opSchema", {"fn": "opSchema"}, lambda: {"schema": holy_sheet.op_schema()}))

    hunk_rng = random.Random(7)
    for k in range(40):
        a = [hunk_rng.choice("abcd") for _ in range(hunk_rng.randint(0, 12))]
        b = [hunk_rng.choice("abcd") for _ in range(hunk_rng.randint(0, 12))]
        cases.append((f"hunks {k}", {"fn": "hunks", "a": a, "b": b}, lambda a=a, b=b: {"hunks": SheetDiff.hunks(a, b)}))

    return cases


def _both_inputs(from_json: Callable[[], Any], native: Callable[[], Any]) -> Any:
    """The JSON-shaped input is what PHP saw; the native one must give the same ops."""
    result = from_json()
    assert _printed(native()) == _printed(result), "the Python-native input gave different ops than its JSON form"
    return result


CASES = _build_cases()


def _printed(value: Any) -> str:
    """The JSON PHP would print, key order kept."""
    return json.dumps(php_json_view(value), ensure_ascii=False, indent=1)


def _run_python(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - compared with PHP's throw, below
        return {"error": type(error).__name__, "message": str(error)}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def php_results(php_oracle) -> list[dict[str, Any]]:
    return php_oracle.php_ops([call for _, call, _ in CASES])


def test_the_batch_covers_every_call_shape() -> None:
    assert {call["fn"] for _, call, _ in CASES} == {"diff", "reduce", "equivalent", "opSchema", "hunks"}
    assert len({case_id for case_id, _, _ in CASES}) == len(CASES)
    assert len(CASES) > 200


@pytest.mark.parametrize("index", range(len(CASES)), ids=[case_id for case_id, _, _ in CASES])
def test_python_gives_phps_answer(php_results: list[dict[str, Any]], index: int) -> None:
    case_id, _, python_call = CASES[index]
    from_php = php_results[index]
    from_python = _run_python(python_call)

    if "error" in from_php or "error" in from_python:
        # Both must throw. PHP's Error / TypeError is Python's TypeError; the class names differ by runtime.
        assert "error" in from_php and "error" in from_python, f"{case_id}: PHP {from_php} / Python {from_python}"
        return

    assert _printed(from_python) == _printed(from_php)


def test_random_runs_actually_change_something(php_results: list[dict[str, Any]]) -> None:
    """A random run that produced no ops would pass parity while testing nothing."""
    diffs = [php_results[i]["ops"] for i, (case_id, _, _) in enumerate(CASES) if case_id.startswith("diff: random run")]
    assert sum(1 for ops in diffs if ops) >= len(diffs) * 0.8
    kinds = {op["type"] for ops in diffs for op in ops}
    assert {"set_cell", "clear_cell", "insert_rows", "delete_rows", "rename_sheet", "add_sheet", "move_sheet"} <= kinds


#: The PHP release this port's ops and op schema match.
PHP_REFERENCE = (2, 3, 1)


def test_the_oracle_is_the_reference_release_or_later() -> None:
    """Fail with the reason when the PHP checkout is older than the reference,
    rather than with a schema diff that reads like a port bug."""
    ok, why = _oracle.oracle_available()
    if not ok:
        pytest.skip(f"PHP oracle unavailable: {why}")
    src = _oracle.php_src_root()
    assert src is not None
    constant = re.search(r"VERSION = '(\d+)\.(\d+)\.(\d+)'", (src / "HolySheet.php").read_text(encoding="utf-8"))
    assert constant is not None, f"no VERSION constant in {src / 'HolySheet.php'}"
    found = tuple(int(part) for part in constant.groups())
    assert found >= PHP_REFERENCE, (
        f"the PHP holy-sheet at {src} is {'.'.join(map(str, found))}; these cases need "
        f"{'.'.join(map(str, PHP_REFERENCE))} or later"
    )
