"""diff / reduce / op_schema / equivalent (holy-sheet #7).

A port of PHP holy-sheet 2.3.1's `tests/Unit/SheetOpsTest.php`, case for case,
followed by the semantics the PHP suite leaves implicit. What a version history
built on these needs, pinned:

1. Round trip: reduce(a, diff(a, b)) equals b.
2. Small edits stay small: one cell is one set_cell, an inserted row is one
   insert_rows plus its cells -- asserted as the exact op TYPES, not just
   "fewer ops than a replace".
3. A save without a change records nothing: diff(s, describe(to_bytes(s))) == [].
4. set_cell / set_range / set_workbook behave as fancy-sheets' reducer does.

`tests/test_sheet_ops_parity_php.py` runs the same edits through PHP and
compares the op lists themselves.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path
from typing import Any, Callable

import pytest

import holy_sheet
from holy_sheet import SheetDiff, SheetOpSchema, SheetReducer
from holy_sheet.ops._php_array import canon, identical, parse_address, php_int_cast, php_json_view

# --------------------------------------------------------------------------
# The PHP fixture and its edits
# --------------------------------------------------------------------------


def workbook() -> dict[str, Any]:
    """PHP's hsWorkbook(). `columnWidths` has integer keys, as PHP's array does."""
    return {
        "sheets": [
            {
                "name": "Q3",
                "cells": {
                    "A1": {"value": "Region", "format": {"bold": True}},
                    "B1": {"value": "Revenue", "format": {"bold": True}},
                    "A2": {"value": "North"},
                    "B2": {"value": 1250000.5, "format": {"displayFormat": "currency", "decimals": 2}},
                    "A3": {"value": "South"},
                    "B3": {"value": 980400.25},
                    "A4": {"value": "West"},
                    "B4": {"value": 1410000},
                    "A5": {"value": "Total", "format": {"bold": True}},
                    "B5": {"value": None, "formula": "SUM(B2:B4)"},
                },
                "mergedRegions": [{"start": "A7", "end": "B7"}],
                "columnWidths": {0: 120, 1: 140},
                "frozenRows": 1,
            },
            {
                "name": "Notes",
                "cells": {"A1": {"value": "Checked by finance", "comment": {"text": "Signed off", "author": "CFO"}}},
            },
        ],
        "meta": {"creator": "MOIC", "created": "2026-09-14T00:00:00Z"},
    }


def _set(w: dict[str, Any], sheet: str, address: str, cell: dict[str, Any]) -> dict[str, Any]:
    for s in w["sheets"]:
        if s["name"] == sheet:
            s["cells"][address] = cell
    return w


def _address(address: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", address)
    assert match is not None
    return match.group(1), int(match.group(2))


def _row_inserted(w: dict[str, Any]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for address, cell in w["sheets"][0]["cells"].items():
        letters, row = _address(address)
        cells[f"{letters}{row + 1 if row >= 3 else row}"] = cell
    cells["A3"] = {"value": "East"}
    cells["B3"] = {"value": 505000}
    cells["B6"] = {"value": None, "formula": "SUM(B2:B5)"}
    w["sheets"][0]["cells"] = cells
    w["sheets"][0]["mergedRegions"] = [{"start": "A8", "end": "B8"}]
    return w


def _row_deleted(w: dict[str, Any]) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for address, cell in w["sheets"][0]["cells"].items():
        letters, row = _address(address)
        if row == 3:
            continue
        cells[f"{letters}{row - 1 if row > 3 else row}"] = cell
    cells["B4"] = {"value": None, "formula": "SUM(B2:B3)"}
    w["sheets"][0]["cells"] = cells
    w["sheets"][0]["mergedRegions"] = [{"start": "A6", "end": "B6"}]
    return w


def _column_inserted(w: dict[str, Any]) -> dict[str, Any]:
    cells = {address.replace("B", "C"): cell for address, cell in w["sheets"][0]["cells"].items()}
    cells["B1"] = {"value": "Units", "format": {"bold": True}}
    cells["B2"] = {"value": 12}
    w["sheets"][0]["cells"] = cells
    w["sheets"][0]["mergedRegions"] = [{"start": "A7", "end": "C7"}]
    w["sheets"][0]["columnWidths"] = {0: 120, 1: 80, 2: 140}
    return w


def _column_deleted(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"][0]["cells"] = {a: c for a, c in w["sheets"][0]["cells"].items() if a[0] != "B"}
    w["sheets"][0]["mergedRegions"] = [{"start": "A7", "end": "A7"}]
    w["sheets"][0]["columnWidths"] = {0: 120}
    return w


def _cell_cleared(w: dict[str, Any]) -> dict[str, Any]:
    del w["sheets"][0]["cells"]["A4"]
    return w


def _sheet_added(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"].insert(1, {"name": "Q4", "cells": {"A1": {"value": "Region"}}})
    return w


def _sheet_removed(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"].pop()
    return w


def _sheet_renamed(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"][1]["name"] = "Sign-off"
    return w


def _sheet_renamed_and_edited(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"][1]["name"] = "Sign-off"
    w["sheets"][1]["cells"]["A2"] = {"value": "Approved"}
    return w


def _sheets_reordered(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"].reverse()
    return w


def _merges_widths_panes(w: dict[str, Any]) -> dict[str, Any]:
    del w["sheets"][0]["mergedRegions"]
    w["sheets"][0]["columnWidths"] = {0: 200, 1: 140}
    w["sheets"][0]["frozenCols"] = 1
    return w


def _meta(w: dict[str, Any]) -> dict[str, Any]:
    w["meta"]["creator"] = "Compass"
    return w


def _authored_sheet_changed(w: dict[str, Any]) -> dict[str, Any]:
    w["sheets"][1] = {"name": "Notes", "columns": [{"header": "Item"}, {"header": "Owner"}], "rows": [["Budget", "CFO"]]}
    return w


#: PHP's `dataset('edits', …)`, in the same order with the same names.
EDITS: dict[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], list[str]]] = {
    "one value": (lambda w: _set(w, "Q3", "B3", {"value": 990000}), ["set_cell"]),
    "one format": (
        lambda w: _set(w, "Q3", "B2", {"value": 1250000.5, "format": {"displayFormat": "currency", "decimals": 0}}),
        ["set_cell"],
    ),
    "a comment removed": (lambda w: _set(w, "Notes", "A1", {"value": "Checked by finance"}), ["set_cell"]),
    "a formula": (lambda w: _set(w, "Q3", "B5", {"value": None, "formula": "SUM(B2:B3)"}), ["set_cell"]),
    "a cell cleared": (_cell_cleared, ["clear_cell"]),
    "a row inserted": (_row_inserted, ["insert_rows", "set_cell", "set_cell", "set_cell"]),
    "a row deleted": (_row_deleted, ["delete_rows", "set_cell"]),
    "a column inserted": (_column_inserted, ["insert_columns", "set_cell", "set_cell", "set_column_widths"]),
    "a column deleted": (_column_deleted, ["delete_columns"]),
    "a sheet added": (_sheet_added, ["add_sheet"]),
    "a sheet removed": (_sheet_removed, ["remove_sheet"]),
    "a sheet renamed": (_sheet_renamed, ["rename_sheet"]),
    "a sheet renamed and edited": (_sheet_renamed_and_edited, ["rename_sheet", "set_cell"]),
    "sheets reordered": (_sheets_reordered, ["move_sheet"]),
    "merges, widths, panes": (_merges_widths_panes, ["set_merged_regions", "set_column_widths", "set_frozen"]),
    "meta": (_meta, ["set_meta"]),
    "an authored sheet changed": (_authored_sheet_changed, ["replace_sheet"]),
}


def edited(name: str) -> dict[str, Any]:
    return EDITS[name][0](workbook())


def _types(ops: list[dict[str, Any]]) -> list[str]:
    return [op["type"] for op in ops]


# --------------------------------------------------------------------------
# Ported from SheetOpsTest.php
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(EDITS))
def test_reproduces_the_target_exactly_with_the_smallest_ops(name: str) -> None:
    a = workbook()
    b = edited(name)

    ops = holy_sheet.diff(a, b)

    assert _types(ops) == EDITS[name][1]
    assert SheetDiff.same(holy_sheet.reduce(a, ops), b)

    # And back: the reverse diff is what a version history stores.
    reverse = holy_sheet.diff(b, a)
    assert SheetDiff.same(holy_sheet.reduce(b, reverse), a)


@pytest.mark.parametrize("name", list(EDITS))
def test_the_same_edit_arriving_as_json_gives_the_same_ops(name: str) -> None:
    """JSON makes `columnWidths` keys strings; the ops must not change for it."""
    a = json.loads(json.dumps(workbook()))
    b = json.loads(json.dumps(edited(name)))

    from_json = holy_sheet.diff(a, b)

    assert _types(from_json) == EDITS[name][1]
    assert json.dumps(php_json_view(from_json)) == json.dumps(php_json_view(holy_sheet.diff(workbook(), edited(name))))
    assert SheetDiff.same(holy_sheet.reduce(a, from_json), b)


def test_records_nothing_for_a_save_without_a_change(tmp_path: Path) -> None:
    described = workbook()
    path = tmp_path / "hs.xlsx"
    path.write_bytes(holy_sheet.to_bytes(described))
    read_back = holy_sheet.describe(str(path))

    assert holy_sheet.diff(described, read_back) == []

    # An AUTHORED schema, with no meta: the writer expands columns/rows into
    # cells and stamps a creation time, and neither is an edit.
    authored = {"sheets": [{
        "name": "Deals",
        "columns": [{"header": "Name"}, {"header": "Value", "type": "currency"}],
        "rows": [["Acme", 1200], ["Globex", 800]],
        "totals": {"Value": "sum"},
    }]}
    path.write_bytes(holy_sheet.to_bytes(authored))
    authored_back = holy_sheet.describe(str(path))

    assert holy_sheet.diff(authored, authored_back) == []
    assert holy_sheet.equivalent(authored, authored_back) is True


def test_falls_back_to_replacing_the_workbook_when_sheets_cannot_be_told_apart() -> None:
    a = {"sheets": [{"name": "Same", "cells": {"A1": {"value": 1}}}, {"name": "Same", "cells": {"A1": {"value": 2}}}]}
    b = {"sheets": [{"name": "Same", "cells": {"A1": {"value": 3}}}]}

    ops = holy_sheet.diff(a, b)

    assert identical(ops, [{"type": "set_workbook", "data": b}])
    assert identical(holy_sheet.reduce(a, ops), b)


def test_keeps_the_round_trip_over_a_seeded_run_of_random_cell_form_edits_without_falling_back() -> None:
    rng = random.Random(20260915)

    for run in range(60):
        a = workbook()
        b = workbook()
        cells = b["sheets"][0]["cells"]

        for _ in range(rng.randint(1, 5)):
            address = chr(65 + rng.randint(0, 3)) + str(rng.randint(1, 9))
            kind = rng.randint(0, 3)
            if kind == 0:
                cells[address] = {"value": rng.randint(1, 999)}
            elif kind == 1:
                cells[address] = {"value": "x" + str(rng.randint(1, 9)), "format": {"italic": True}}
            elif kind == 2:
                cells[address] = {"value": None, "formula": "SUM(B2:B" + str(rng.randint(3, 6)) + ")"}
            else:
                cells.pop(address, None)

        ops = holy_sheet.diff(a, b)

        assert SheetDiff.same(holy_sheet.reduce(a, ops), b), f"run {run}"
        assert not {"set_workbook", "replace_sheet"} & set(_types(ops)), f"run {run} fell back"


def test_set_cell_set_range_and_set_workbook_behave_as_fancy_sheets_reduce_workbook_does() -> None:
    w = workbook()

    # No formula in the op clears the formula; the format stays.
    cleared = holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Q3", "address": "B5", "value": 42})
    assert identical(cleared["sheets"][0]["cells"]["B5"], {"value": 42})

    kept = holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Q3", "address": "A1", "value": "Area"})
    assert identical(kept["sheets"][0]["cells"]["A1"], {"value": "Area", "format": {"bold": True}})

    # A null write to an absent cell does nothing.
    assert identical(holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Q3", "address": "Z99", "value": None}), w)

    # set_range writes values row-major from start.
    ranged = holy_sheet.reduce(w, {"type": "set_range", "sheet": "Q3", "start": "C2", "values": [[1, 2], [3, 4]]})
    assert identical(ranged["sheets"][0]["cells"]["D3"], {"value": 4})

    # An unknown sheet is skipped, and the input is never modified.
    assert identical(holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Nope", "address": "A1", "value": 1}), w)
    assert identical(w, workbook())

    assert identical(holy_sheet.reduce(w, {"type": "set_workbook", "data": {"sheets": []}}), {"sheets": []})


def test_moves_cells_merges_and_widths_on_row_and_column_inserts_and_deletes() -> None:
    w = {"sheets": [{
        "name": "S",
        "cells": {"A1": {"value": 1}, "A2": {"value": 2}, "C3": {"value": 3}},
        "mergedRegions": [{"start": "A2", "end": "C4"}],
        "columnWidths": {0: 10, 2: 30},
    }]}

    rows = holy_sheet.reduce(w, {"type": "insert_rows", "sheet": "S", "at": 2, "count": 2})
    assert list(rows["sheets"][0]["cells"]) == ["A1", "A4", "C5"]
    assert rows["sheets"][0]["mergedRegions"] == [{"start": "A4", "end": "C6"}]

    deleted = holy_sheet.reduce(w, {"type": "delete_rows", "sheet": "S", "at": 2, "count": 1})
    assert list(deleted["sheets"][0]["cells"]) == ["A1", "C2"]
    assert deleted["sheets"][0]["mergedRegions"] == [{"start": "A2", "end": "C3"}]

    cols = holy_sheet.reduce(w, {"type": "delete_columns", "sheet": "S", "at": 1, "count": 1})
    assert list(cols["sheets"][0]["cells"]) == ["B3"]
    assert identical(cols["sheets"][0]["columnWidths"], {1: 30})
    assert cols["sheets"][0]["mergedRegions"] == [{"start": "A2", "end": "B4"}]


def test_publishes_one_schema_variant_per_op_type() -> None:
    schema = holy_sheet.op_schema()

    assert schema == SheetOpSchema.json_schema()
    assert tuple(v["properties"]["type"]["const"] for v in schema["oneOf"]) == SheetOpSchema.TYPES


def test_emits_ops_its_own_schema_accepts_when_every_column_width_is_removed() -> None:
    # PHP 2.3.1. Removing every width emits `columnWidths: []`, PHP's JSON for
    # an empty map, which the 2.3.0 schema declared an object only -- so a host
    # validating stored ops with op_schema() rejected a diff's output.
    a = workbook()
    b = workbook()
    del b["sheets"][0]["columnWidths"]

    ops = holy_sheet.diff(a, b)
    assert len(ops) == 1
    assert ops[0]["type"] == "set_column_widths"

    variant = next(v for v in holy_sheet.op_schema()["oneOf"] if v["properties"]["type"]["const"] == "set_column_widths")
    declared = variant["properties"]["columnWidths"]["type"]

    assert json.dumps(php_json_view(ops[0]["columnWidths"])) == "[]"
    assert ops[0]["columnWidths"] == []  # the same value PHP's diff holds, not Node's {}
    assert "array" in declared
    assert variant["properties"]["columnWidths"]["maxItems"] == 0
    assert SheetDiff.same(holy_sheet.reduce(a, ops), b)


def test_aligns_rows_by_content_breaking_ties_toward_deleting_first() -> None:
    assert SheetDiff.hunks(["a", "b", "c"], ["a", "x", "b", "c"]) == [[1, 0, 1]]
    assert SheetDiff.hunks(["a", "b", "c"], ["a", "c"]) == [[1, 1, 0]]
    assert SheetDiff.hunks(["a", "b"], ["a", "z"]) == [[1, 1, 1]]
    assert SheetDiff.hunks(["a"], ["a"]) == []


# --------------------------------------------------------------------------
# What the PHP suite leaves implicit
# --------------------------------------------------------------------------


def test_diff_only_emits_types_the_op_schema_publishes() -> None:
    for name in EDITS:
        for ops in (holy_sheet.diff(workbook(), edited(name)), holy_sheet.diff(edited(name), workbook())):
            assert set(_types(ops)) <= set(SheetOpSchema.TYPES), name


def test_every_variant_requires_type_and_forbids_other_keys() -> None:
    for variant in holy_sheet.op_schema()["oneOf"]:
        assert variant["required"][0] == "type"
        assert variant["additionalProperties"] is False
        assert set(variant["required"]) <= set(variant["properties"])


def test_op_schema_is_a_fresh_copy_on_every_call() -> None:
    first = holy_sheet.op_schema()
    first["oneOf"][0]["properties"]["address"]["pattern"] = "mutated"
    assert holy_sheet.op_schema()["oneOf"][0]["properties"]["address"]["pattern"] == "^[A-Za-z]+[0-9]+$"


def test_hunks_skips_alignment_past_the_limit() -> None:
    a = [f"a{i}" for i in range(501)]
    b = [f"b{i}" for i in range(500)]
    assert SheetDiff.ALIGN_LIMIT == 250_000
    assert SheetDiff.hunks(a, b) == [[0, 501, 500]]  # 250,500 cells of work: one hunk
    assert SheetDiff.hunks(["same", *a[:500], "end"], ["same", *b, "end"]) == [[1, 500, 500]]


class TestSetCell:
    """fancy-sheets' set_cell, and the cell parts it does not carry."""

    @staticmethod
    def _cell(op: dict[str, Any], w: dict[str, Any] | None = None) -> Any:
        result = holy_sheet.reduce(w or workbook(), {"type": "set_cell", "sheet": "Q3", **op})
        return result["sheets"][0]["cells"].get(op["address"].upper(), "ABSENT")

    def test_omitting_formula_or_computed_value_clears_them(self) -> None:
        w = _set(workbook(), "Q3", "C1", {"value": 3, "formula": "1+2", "computedValue": 3})
        assert identical(self._cell({"address": "C1", "value": 4}, w), {"value": 4})

    def test_omitting_format_or_comment_keeps_them_and_null_clears_them(self) -> None:
        w = _set(workbook(), "Q3", "C1", {"value": 1, "format": {"bold": True}, "comment": {"text": "hi"}})
        assert identical(self._cell({"address": "C1", "value": 2}, w), {"value": 2, "format": {"bold": True}, "comment": {"text": "hi"}})
        assert identical(self._cell({"address": "C1", "value": 2, "format": None}, w), {"value": 2, "comment": {"text": "hi"}})
        assert identical(self._cell({"address": "C1", "value": 2, "comment": None}, w), {"value": 2, "format": {"bold": True}})
        assert identical(self._cell({"address": "C1", "value": 2, "format": {"italic": True}}, w), {"value": 2, "format": {"italic": True}, "comment": {"text": "hi"}})

    def test_an_op_without_value_writes_a_cell_without_value(self) -> None:
        assert identical(self._cell({"address": "C1", "formula": "SUM(B2:B4)"}), {"formula": "SUM(B2:B4)"})

    def test_a_null_write_carrying_nothing_else_to_an_absent_cell_is_a_no_op(self) -> None:
        w = workbook()
        for op in ({"value": None}, {"value": None, "formula": None, "format": None, "comment": None}, {}):
            result = holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Q3", "address": "Z9", **op})
            assert identical(result, w)
            assert "Z9" not in result["sheets"][0]["cells"]

    def test_a_resulting_empty_cell_is_removed(self) -> None:
        # An existing cell written with nothing at all keeps nothing, so it goes.
        w = _set(workbook(), "Q3", "C1", {"value": 1, "formula": "1"})
        assert self._cell({"address": "C1"}, w) == "ABSENT"

    def test_the_address_is_upper_cased_and_an_invalid_one_is_skipped(self) -> None:
        assert identical(self._cell({"address": "c9", "value": 1}), {"value": 1})
        w = workbook()
        assert identical(holy_sheet.reduce(w, {"type": "set_cell", "sheet": "Q3", "address": "9C", "value": 1}), w)

    def test_a_padded_address_is_stored_untrimmed_as_php_does(self) -> None:
        # PHP upper-cases the address, validates the TRIMMED form, and keys the
        # cell by the untrimmed one. Mirrored, not fixed.
        result = holy_sheet.reduce({"sheets": [{"name": "A", "cells": {}}]}, {"type": "set_cell", "sheet": "A", "address": " a1 ", "value": 1})
        assert list(result["sheets"][0]["cells"]) == [" A1 "]


class TestStructure:
    def test_sheet_ops(self) -> None:
        w = workbook()
        added = holy_sheet.reduce(w, {"type": "add_sheet", "index": 99, "sheet": {"name": "Z"}})
        assert [s["name"] for s in added["sheets"]] == ["Q3", "Notes", "Z"]
        added = holy_sheet.reduce(w, {"type": "add_sheet", "index": -4, "sheet": {"name": "Z"}})
        assert [s["name"] for s in added["sheets"]] == ["Z", "Q3", "Notes"]
        assert identical(holy_sheet.reduce(w, {"type": "add_sheet", "sheet": "not a sheet"}), w)

        assert [s["name"] for s in holy_sheet.reduce(w, {"type": "remove_sheet", "sheet": "Q3"})["sheets"]] == ["Notes"]
        assert [s["name"] for s in holy_sheet.reduce(w, {"type": "rename_sheet", "sheet": "Q3", "name": "Q4"})["sheets"]] == ["Q4", "Notes"]
        assert [s["name"] for s in holy_sheet.reduce(w, {"type": "move_sheet", "sheet": "Q3", "toIndex": 7})["sheets"]] == ["Notes", "Q3"]

        replaced = holy_sheet.reduce(w, {"type": "replace_sheet", "sheet": "Notes", "data": {"name": "Notes", "rows": [["x"]]}})
        assert identical(replaced["sheets"][1], {"name": "Notes", "rows": [["x"]]})

        for op in ({"type": "remove_sheet", "sheet": "Nope"}, {"type": "no_such_op", "sheet": "Q3"}, {"sheet": "Q3"}):
            assert identical(holy_sheet.reduce(w, op), w)

    def test_empty_merges_widths_and_zero_panes_remove_the_key(self) -> None:
        w = workbook()
        sheet = holy_sheet.reduce(w, [
            {"type": "set_merged_regions", "sheet": "Q3", "mergedRegions": []},
            {"type": "set_column_widths", "sheet": "Q3", "columnWidths": {}},
            {"type": "set_frozen", "sheet": "Q3", "rows": 0, "cols": 0},
        ])["sheets"][0]
        assert list(sheet) == ["name", "cells"]

        sheet = holy_sheet.reduce(w, {"type": "set_frozen", "sheet": "Q3", "rows": 2, "cols": 1})["sheets"][0]
        assert (sheet["frozenRows"], sheet["frozenCols"]) == (2, 1)

    def test_set_meta_replaces_or_removes(self) -> None:
        w = workbook()
        assert holy_sheet.reduce(w, {"type": "set_meta", "meta": {"creator": "X"}})["meta"] == {"creator": "X"}
        assert "meta" not in holy_sheet.reduce(w, {"type": "set_meta", "meta": None})

    def test_clear_cell_removes_and_ignores_an_absent_cell(self) -> None:
        w = workbook()
        assert "A4" not in holy_sheet.reduce(w, {"type": "clear_cell", "sheet": "Q3", "address": "a4"})["sheets"][0]["cells"]
        assert identical(holy_sheet.reduce(w, {"type": "clear_cell", "sheet": "Q3", "address": "Z9"}), w)

    def test_a_delete_shrinks_or_drops_a_merge_it_cuts(self) -> None:
        w = {"sheets": [{"name": "S", "mergedRegions": [{"start": "A2", "end": "A5"}, {"start": "B3", "end": "B3"}]}]}
        result = holy_sheet.reduce(w, {"type": "delete_rows", "sheet": "S", "at": 3, "count": 2})
        assert result["sheets"][0]["mergedRegions"] == [{"start": "A2", "end": "A3"}]
        gone = holy_sheet.reduce({"sheets": [{"name": "S", "mergedRegions": [{"start": "B3", "end": "B3"}]}]}, {"type": "delete_rows", "sheet": "S", "at": 3, "count": 1})
        assert "mergedRegions" not in gone["sheets"][0]

    def test_shifted_cells_come_back_row_major(self) -> None:
        w = {"sheets": [{"name": "S", "cells": {"C1": {"value": 1}, "A2": {"value": 2}, "B1": {"value": 3}}}]}
        result = holy_sheet.reduce(w, {"type": "insert_columns", "sheet": "S", "at": 5, "count": 1})
        assert list(result["sheets"][0]["cells"]) == ["B1", "C1", "A2"]


class TestColumnWidthKeys:
    """JSON makes `columnWidths` keys strings; PHP makes them ints with `(int)`."""

    def test_a_column_shift_casts_keys_as_php_does_and_returns_int_keys(self) -> None:
        w = {"sheets": [{"name": "S", "columnWidths": {"abc": 5, "3": 7, "1.5": 9}}]}
        result = holy_sheet.reduce(w, {"type": "insert_columns", "sheet": "S", "at": 1, "count": 1})
        # PHP 8.4 prints {"1":5,"2":9,"4":7} for the same input.
        assert identical(result["sheets"][0]["columnWidths"], {1: 5, 2: 9, 4: 7})
        assert all(type(key) is int for key in result["sheets"][0]["columnWidths"])

    def test_string_and_int_keys_are_one_key_to_same(self) -> None:
        assert SheetDiff.same({"columnWidths": {"0": 120, "1": 140}}, {"columnWidths": {0: 120, 1: 140}})
        assert SheetDiff.same({"columnWidths": {"1": 140, "0": 120}}, {"columnWidths": [120, 140]})

    def test_set_column_widths_stores_the_map_as_given(self) -> None:
        result = holy_sheet.reduce(workbook(), {"type": "set_column_widths", "sheet": "Q3", "columnWidths": {"0": 90}})
        assert identical(result["sheets"][0]["columnWidths"], {"0": 90})


class TestPurity:
    def test_the_reducer_classes_are_pure_too(self) -> None:
        w = workbook()
        op = {"type": "rename_sheet", "sheet": "Notes", "name": "Sign-off"}
        assert SheetReducer.apply(w, op)["sheets"][1]["name"] == "Sign-off"
        assert SheetReducer.apply_all(w, [op, {"type": "remove_sheet", "sheet": "Q3"}])["sheets"] == [
            {"name": "Sign-off", "cells": w["sheets"][1]["cells"]}
        ]
        assert identical(w, workbook())
        assert SheetReducer.CELL_FORM_KEYS == ("name", "cells", "mergedRegions", "columnWidths", "frozenRows", "frozenCols")

    def test_the_result_shares_nothing_with_the_schema_or_the_ops(self) -> None:
        w = workbook()
        fmt = {"italic": True}
        op = {"type": "set_cell", "sheet": "Q3", "address": "C1", "value": 1, "format": fmt}
        result = holy_sheet.reduce(w, op)

        result["sheets"][0]["cells"]["C1"]["format"]["italic"] = False
        result["sheets"][1]["cells"]["A1"]["value"] = "changed"
        result["meta"]["creator"] = "changed"

        assert fmt == {"italic": True}
        assert identical(w, workbook())

    def test_diff_ops_do_not_alias_the_target(self) -> None:
        b = edited("a sheet added")
        ops = holy_sheet.diff(workbook(), b)
        ops[0]["sheet"]["name"] = "changed"
        assert b["sheets"][1]["name"] == "Q4"

    def test_reduce_takes_one_op_a_list_or_nothing(self) -> None:
        w = workbook()
        op = {"type": "set_meta", "meta": None}
        assert "meta" not in holy_sheet.reduce(w, op)
        assert "meta" not in holy_sheet.reduce(w, [op])
        assert identical(holy_sheet.reduce(w, []), w)
        assert identical(holy_sheet.reduce(w, {}), w)
        with pytest.raises(TypeError):
            holy_sheet.reduce(w, "set_meta")


class TestPhpSemantics:
    """Where Python's builtins disagree with the PHP arrays the reference runs on."""

    def test_same_tells_bool_from_int_and_int_from_float(self) -> None:
        # Python: True == 1 == 1.0. PHP's canonical JSON: true, 1 and 1.0 are three values.
        assert not SheetDiff.same({"value": True}, {"value": 1})
        assert not SheetDiff.same({"value": 1}, {"value": 1.0})
        assert not SheetDiff.same([False], [0])
        assert SheetDiff.same({"a": 1, "b": [1, 2]}, {"b": [1, 2], "a": 1})
        assert not SheetDiff.same([1, 2], [2, 1])
        assert SheetDiff.same({}, [])

    def test_a_bool_edit_is_a_change_the_diff_records(self) -> None:
        a = {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}}}]}
        b = {"sheets": [{"name": "S", "cells": {"A1": {"value": True}}}]}
        ops = holy_sheet.diff(a, b)
        assert identical(ops, [{"type": "set_cell", "sheet": "S", "address": "A1", "value": True}])

    def test_frozen_panes_compare_typed(self) -> None:
        # `!==` in PHP: 2.0 is not 2. The set_frozen op carries 2.0, the
        # reducer's (int) cast writes 2, the per-sheet verification sees 2 is not
        # 2.0, and the sheet is replaced whole -- exact, if not small.
        a = {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}}, "frozenRows": 1}]}
        b = {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}}, "frozenRows": 2.0}]}
        ops = holy_sheet.diff(a, b)
        assert _types(ops) == ["replace_sheet"]
        assert identical(holy_sheet.reduce(a, ops), b)

    def test_canon_of_what_json_encode_rejects_is_empty(self) -> None:
        assert canon(float("nan")) == ""
        assert canon({"a": float("inf")}) == ""
        assert canon("\ud800") == ""
        deep: Any = 1
        for _ in range(512):
            deep = [deep]
        assert canon(deep) != ""
        assert canon([deep]) == ""

    @pytest.mark.parametrize(
        ("value", "expected"),
        # Every expectation here is what PHP 8.4 printed for `(int) $value`.
        [
            (1e20, 7766279631452241920),
            ("99999999999999999999", 9223372036854775807),
            (" 12abc", 12),
            ("1e3", 1000),
            ("1e3abc", 1000),
            ("1.9e1x", 19),
            ("1.9", 1),
            (".5", 0),
            ("  +3", 3),
            ("2 ", 2),
            ("\n5", 5),
            ("abc", 0),
            ("1e400", 0),
            ("-99999999999999999999", -9223372036854775808),
            (float("nan"), 0),
            (-1e19, 8446744073709551616),
            (-1.9, -1),
            ([], 0),
            ([0], 1),
            (True, 1),
            (None, 0),
        ],
    )
    def test_int_cast_is_php_8_4s(self, value: Any, expected: int) -> None:
        assert php_int_cast(value) == expected

    @pytest.mark.parametrize(
        ("address", "expected"),
        # PHP 8.4 `CellAddress::parse` printed null, null, null and [0,1] for these.
        # This package's own `CellAddress.parse` answers (161, 1), (0, 1), (0, 1)
        # and None: Unicode upper-casing, Unicode digits, Unicode trimming.
        [("\ufb001", None), ("A\u0661", None), ("\u00a0A1", None), ("\x00A1", (0, 1)), (" b12 ", (1, 12))],
    )
    def test_ops_parse_addresses_as_php_does(self, address: str, expected: Any) -> None:
        assert parse_address(address) == expected

    def test_a_true_type_matches_php_switchs_first_case(self) -> None:
        # PHP's `switch` compares loosely and `true == 'remove_sheet'`. Mirrored, not fixed.
        w = {"sheets": [{"name": "A", "cells": {}}, {"name": "B"}]}
        assert [s["name"] for s in holy_sheet.reduce(w, {"type": True, "sheet": "B"})["sheets"]] == ["A"]
        assert identical(holy_sheet.reduce(w, {"type": 1, "sheet": "B"}), w)


class TestEquivalent:
    def test_created_counts_only_when_both_schemas_name_one(self) -> None:
        a = workbook()
        b = copy.deepcopy(a)
        b["meta"]["created"] = "2020-01-01T00:00:00Z"
        assert holy_sheet.equivalent(a, b) is False

        del b["meta"]["created"]
        assert holy_sheet.equivalent(a, b) is True

    def test_a_meta_left_empty_is_dropped(self) -> None:
        a = {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}}}]}
        b = {"sheets": [{"name": "S", "cells": {"A1": {"value": 1}}}], "meta": {"created": "2020-01-01T00:00:00Z"}}
        assert holy_sheet.equivalent(a, b) is True

    def test_what_the_writer_normalises_is_not_a_change(self) -> None:
        a = workbook()
        b = copy.deepcopy(a)
        b["sheets"][0]["columnWidths"] = {"0": 120.0, "1": 140.0}
        b["sheets"][0]["frozenCols"] = 0
        assert SheetDiff.same(a, b) is False
        assert holy_sheet.diff(a, b) == []

    def test_a_null_cell_is_not_nothing_to_the_writer(self) -> None:
        # It is written, and reads back carrying a format, so it IS a change.
        a = workbook()
        b = copy.deepcopy(a)
        b["sheets"][0]["cells"]["C9"] = {"value": None}
        assert holy_sheet.equivalent(a, b) is False
        # set_cell cannot write a null cell carrying nothing, so the sheet is replaced.
        ops = holy_sheet.diff(a, b)
        assert _types(ops) == ["replace_sheet"]
        assert SheetDiff.same(holy_sheet.reduce(a, ops), b)

    def test_an_invalid_schema_raises(self) -> None:
        with pytest.raises(holy_sheet.SchemaException):
            holy_sheet.diff(workbook(), {"sheets": "nope"})
