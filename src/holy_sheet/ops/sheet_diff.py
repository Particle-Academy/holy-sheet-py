"""The op list that turns one Holy Sheet schema into another.

Mirrors PHP `Ops\\SheetDiff` (holy-sheet 2.3.3), which is normative. The same
inputs give the same ops, in the same order, in both runtimes;
`tests/test_sheet_ops_parity_php.py` runs PHP as a subprocess and compares.

## Two guarantees, and where each applies

1. **Same workbook, no ops.** When `a` and `b` write the same workbook --
   compared as `read(to_bytes(...))`, so a columns/rows sheet and the cell map
   it becomes are the same, and the timestamp the writer stamps on a schema
   that names none is not a change -- the diff is `[]`. Saving without a change
   records nothing.
2. **Otherwise, exact.** `reduce(a, diff(a, b))` equals `b`, key order aside.
   The ops are computed on the schemas as given and VERIFIED by replaying them
   through `SheetReducer`; a sheet whose granular ops do not reproduce
   it is replaced whole, and a workbook that still does not match is replaced
   whole. Correct first, small second.

## Small edits stay small

Rows and columns are ALIGNED before cells are compared (a longest common
subsequence over each row's, then each column's, contents), so inserting a row
above a hundred others is one `insert_rows` plus the new row's cells, not a
hundred rewritten cells. One changed cell is one `set_cell`.

Granular ops apply to sheets in CELL form -- the form `describe()` returns. A
sheet authored as columns/rows/theme/totals, on either side, is replaced whole
when it changes.

## Equality is PHP's

`same()` compares canonical JSON with map keys sorted and list order kept,
built with PHP's array semantics (see `_php_array.canon`): `1` and `1.0`
differ, `True` and `1` differ, `[]` and `{}` are the same, `{"0": x}` is `[x]`.
Python's own `==` would get the first two wrong, because `bool` subclasses
`int` and `1 == 1.0`.
"""

from __future__ import annotations

import copy
from typing import Any

from ._php_array import (
    canon,
    get,
    identical,
    is_array,
    php_key,
    php_pairs,
    php_string_cast,
    parse_address,
    values,
)
from .sheet_reducer import CELL_FORM_KEYS, apply_all_shared, apply_shared


class SheetDiff:
    """Namespace class, mirroring PHP's static `SheetDiff`."""

    #: Above this many LCS cells (after trimming the common prefix and suffix),
    #: rows or columns are not aligned.
    ALIGN_LIMIT = 250_000

    @staticmethod
    def diff(a: Any, b: Any) -> list[dict[str, Any]]:
        """The ops that turn `a` into `b`. Both must be valid schemas."""
        _require_array(a, "a")
        _require_array(b, "b")

        if SheetDiff.same(a, b) or SheetDiff.equivalent(a, b):
            return []

        ops = _granular(a, b)

        if SheetDiff.same(apply_all_shared(a, ops), b):
            return copy.deepcopy(ops)

        return [{"type": "set_workbook", "data": copy.deepcopy(b)}]

    @staticmethod
    def equivalent(a: Any, b: Any) -> bool:
        """Whether two schemas write the same workbook.

        Both are written with this port's `to_bytes` and read back with `read`.
        `meta.created` is compared only when both schemas name one: the writer
        stamps the current time on a schema that does not, and that timestamp is
        not an edit. A `meta` left empty by dropping it is dropped too.
        """
        _require_array(a, "a")
        _require_array(b, "b")

        described_a = _described(a)
        described_b = _described(b)

        if not _names_created(a) or not _names_created(b):
            for described in (described_a, described_b):
                _drop_created(described)

        return SheetDiff.same(described_a, described_b)

    @staticmethod
    def same(a: Any, b: Any) -> bool:
        """Structural equality with map key order ignored and list order kept.

        Raises `ValueError` on a value JSON cannot hold (NaN, an infinity, a lone
        surrogate, more than 4096 nested arrays), as PHP 2.3.2 throws
        `JsonException`, rather than calling two of them the same.
        """
        return canon(a) == canon(b)

    @staticmethod
    def hunks(a: list[str], b: list[str]) -> list[list[int]]:
        """Hunks of a longest-common-subsequence alignment: [start in a, deleted, inserted].

        Ties break toward deleting first. Past `ALIGN_LIMIT` the changed middle
        is one hunk.
        """
        n = len(a)
        m = len(b)
        prefix = 0

        while prefix < n and prefix < m and a[prefix] == b[prefix]:
            prefix += 1

        suffix = 0

        while suffix < n - prefix and suffix < m - prefix and a[n - 1 - suffix] == b[m - 1 - suffix]:
            suffix += 1

        mid_a = a[prefix : n - suffix]
        mid_b = b[prefix : m - suffix]
        rows = len(mid_a)
        cols = len(mid_b)

        if rows == 0 and cols == 0:
            return []

        if rows * cols > SheetDiff.ALIGN_LIMIT:
            return [[prefix, rows, cols]]

        # lengths[i][j] = LCS of mid_a[i:] and mid_b[j:]
        lengths = [[0] * (cols + 1) for _ in range(rows + 1)]

        for i in range(rows - 1, -1, -1):
            here = lengths[i]
            below = lengths[i + 1]
            for j in range(cols - 1, -1, -1):
                if mid_a[i] == mid_b[j]:
                    here[j] = below[j + 1] + 1
                else:
                    here[j] = below[j] if below[j] >= here[j + 1] else here[j + 1]

        hunks: list[list[int]] = []
        open_hunk: list[int] | None = None
        i = 0
        j = 0

        while i < rows or j < cols:
            if i < rows and j < cols and mid_a[i] == mid_b[j]:
                if open_hunk is not None:
                    hunks.append(open_hunk)
                    open_hunk = None
                i += 1
                j += 1
                continue

            if open_hunk is None:
                open_hunk = [prefix + i, 0, 0]

            if j >= cols or (i < rows and lengths[i + 1][j] >= lengths[i][j + 1]):
                open_hunk[1] += 1
                i += 1
            else:
                open_hunk[2] += 1
                j += 1

        if open_hunk is not None:
            hunks.append(open_hunk)

        return hunks


def _require_array(value: Any, name: str) -> None:
    if not is_array(value):
        raise TypeError(f"[holy-sheet] {name} must be an array (dict or list), got {type(value).__name__}")


def _described(schema: Any) -> dict[str, Any]:
    # Imported here: agent.py imports this module.
    from ..agent import read, to_bytes

    return read(to_bytes(schema))


def _names_created(schema: Any) -> bool:
    """`isset($schema['meta']['created'])`."""
    return get(get(schema, "meta"), "created") is not None


def _drop_created(described: dict[str, Any]) -> None:
    """`unset($d['meta']['created']); if (($d['meta'] ?? null) === []) unset($d['meta']);`

    `described` is this call's own fresh read, so it is changed in place.
    """
    if "meta" not in described:
        return
    meta = described["meta"]
    if isinstance(meta, dict):
        meta = {key: item for key, item in meta.items() if php_key(key) != "created"}
        described["meta"] = meta
    elif isinstance(meta, str):
        raise TypeError("Cannot unset string offsets")
    elif meta is not None and not is_array(meta):
        raise TypeError("Cannot unset offset in a non-array variable")
    if is_array(meta) and len(meta) == 0:
        del described["meta"]


def _sheet_name(sheet: Any) -> str:
    # PHP's closure is typed `fn (array $s): string`, so a non-array sheet is a TypeError there.
    if not is_array(sheet):
        raise TypeError(f"[holy-sheet] a sheet must be an array, got {type(sheet).__name__}")
    return php_string_cast(get(sheet, "name", ""))


def _granular(a: Any, b: Any) -> list[dict[str, Any]]:
    raw_a = get(a, "sheets")
    raw_b = get(b, "sheets")
    sheets_a = values(raw_a) if is_array(raw_a) else []
    sheets_b = values(raw_b) if is_array(raw_b) else []
    names_a = [_sheet_name(sheet) for sheet in sheets_a]
    names_b = [_sheet_name(sheet) for sheet in sheets_b]

    # Sheets are addressed by name. Two with one name cannot be told apart.
    if len(set(names_a)) != len(names_a) or len(set(names_b)) != len(names_b):
        return [{"type": "set_workbook", "data": b}]

    ops: list[dict[str, Any]] = []

    if not SheetDiff.same(get(a, "meta"), get(b, "meta")):
        ops.append({"type": "set_meta", "meta": get(b, "meta")})

    in_a = set(names_a)
    in_b = set(names_b)
    removed = [name for name in names_a if name not in in_b]
    added = [name for name in names_b if name not in in_a]
    renames: dict[str, str] = {}

    # A sheet whose contents are unchanged under a new name is a rename.
    for old in removed:
        for new in added:
            if new in renames.values():
                continue
            if SheetDiff.same(
                _without_name(sheets_a[names_a.index(old)]),
                _without_name(sheets_b[names_b.index(new)]),
            ):
                renames[old] = new
                break

    # One sheet gone and one arrived is a rename, possibly with edits.
    unmatched_removed = [name for name in removed if name not in renames]
    unmatched_added = [name for name in added if name not in renames.values()]

    if len(unmatched_removed) == 1 and len(unmatched_added) == 1:
        renames[unmatched_removed[0]] = unmatched_added[0]
        unmatched_removed = []
        unmatched_added = []

    for name in unmatched_removed:
        ops.append({"type": "remove_sheet", "sheet": name})

    for name in names_a:
        if name in renames:
            ops.append({"type": "rename_sheet", "sheet": name, "name": renames[name]})

    for index, name in enumerate(names_b):
        if name in unmatched_added:
            ops.append({"type": "add_sheet", "index": index, "sheet": sheets_b[index]})

    # Put the sheets in b's order.
    state = apply_all_shared(a, ops)
    for index, name in enumerate(names_b):
        raw_state = get(state, "sheets", [])
        current = {key: _sheet_name(sheet) for key, sheet in php_pairs(raw_state)}
        if current.get(index) != name:
            op = {"type": "move_sheet", "sheet": name, "toIndex": index}
            ops.append(op)
            state = apply_shared(state, op)

    state_sheets = dict(php_pairs(get(state, "sheets", [])))
    for index, target in enumerate(sheets_b):
        current_sheet = state_sheets.get(index)

        if SheetDiff.same(current_sheet, target):
            continue

        ops.extend(_sheet_ops(current_sheet, target))

    return ops


def _sheet_ops(sheet: Any, target: Any) -> list[dict[str, Any]]:
    if not is_array(sheet):
        raise TypeError(f"[holy-sheet] a sheet must be an array, got {type(sheet).__name__}")

    name = php_string_cast(get(target, "name"))
    replace = [{"type": "replace_sheet", "sheet": name, "data": target}]

    if not _is_cell_form(sheet) or not _is_cell_form(target):
        return replace

    ops: list[dict[str, Any]] = []
    work = sheet

    for axis in ("row", "col"):
        for op in _structural_ops(name, work, target, axis):
            ops.append(op)
            work = _apply_to_sheet(work, op)

    raw_w = get(work, "cells")
    raw_t = get(target, "cells")
    cells_w = raw_w if is_array(raw_w) else []
    cells_t = raw_t if is_array(raw_t) else []
    keys_t = {key: cell for key, cell in php_pairs(cells_t)}
    keys_w = {key: cell for key, cell in php_pairs(cells_w)}

    for address in _row_major(_unique_strings(list(keys_w) + list(keys_t))):
        key = php_key(address)

        if key not in keys_t:
            ops.append({"type": "clear_cell", "sheet": name, "address": address})
            continue

        was = keys_w.get(key)

        if was is not None and SheetDiff.same(was, keys_t[key]):
            continue

        ops.append(_set_cell_op(name, address, was if is_array(was) else None, _to_array(keys_t[key])))

    if not SheetDiff.same(get(work, "mergedRegions", []), get(target, "mergedRegions", [])):
        ops.append({"type": "set_merged_regions", "sheet": name, "mergedRegions": get(target, "mergedRegions", [])})

    if not SheetDiff.same(get(work, "columnWidths", []), get(target, "columnWidths", [])):
        ops.append({"type": "set_column_widths", "sheet": name, "columnWidths": get(target, "columnWidths", [])})

    # `!==`: typed. Python's `!=` would call 1 and 1.0, or True and 1, the same.
    if not identical(get(work, "frozenRows", 0), get(target, "frozenRows", 0)) or not identical(
        get(work, "frozenCols", 0), get(target, "frozenCols", 0)
    ):
        ops.append({
            "type": "set_frozen",
            "sheet": name,
            "rows": get(target, "frozenRows", 0),
            "cols": get(target, "frozenCols", 0),
        })

    # Verified per sheet, so one sheet the granular ops cannot reproduce is
    # replaced without costing the rest of the workbook its small diff.
    result = sheet
    for op in ops:
        result = _apply_to_sheet(result, op)

    return ops if SheetDiff.same(result, target) else replace


def _set_cell_op(sheet: str, address: str, was: Any, cell: Any) -> dict[str, Any]:
    op: dict[str, Any] = {"type": "set_cell", "sheet": sheet, "address": address}

    if isinstance(cell, dict) and "value" in cell:
        op["value"] = cell["value"]

    for key in ("formula", "computedValue"):
        if isinstance(cell, dict) and key in cell:
            op[key] = cell[key]

    for key in ("format", "comment"):
        if not SheetDiff.same(get(was, key), get(cell, key)):
            op[key] = get(cell, key)

    return op


def _structural_ops(name: str, sheet: Any, target: Any, axis: str) -> list[dict[str, Any]]:
    """Row (or column) inserts and deletes, found by aligning contents.

    Emitted from the bottom (or right) up, so each op's position is still the
    position in the sheet as it was.
    """
    lines = _lines(sheet, axis)
    target_lines = _lines(target, axis)
    ops: list[dict[str, Any]] = []

    for start, deleted, inserted in reversed(SheetDiff.hunks(lines, target_lines)):
        kept = min(deleted, inserted)
        at = start + kept + 1

        if deleted > inserted:
            ops.append({
                "type": "delete_rows" if axis == "row" else "delete_columns",
                "sheet": name,
                "at": at,
                "count": deleted - inserted,
            })
        elif inserted > deleted and at <= len(lines):
            # Past the last row there is nothing to move down.
            ops.append({
                "type": "insert_rows" if axis == "row" else "insert_columns",
                "sheet": name,
                "at": at,
                "count": inserted - deleted,
            })

    return ops


def _lines(sheet: Any, axis: str) -> list[str]:
    """Each row's (or column's) contents, as a comparable string, index 0 = row 1."""
    grouped: dict[int, dict[int, Any]] = {}
    maximum = 0

    raw_cells = get(sheet, "cells")
    for address, cell in php_pairs(raw_cells if is_array(raw_cells) else []):
        parsed = parse_address(php_string_cast(address))
        if parsed is None:
            continue
        col, row = parsed
        line = row if axis == "row" else col + 1
        position = col if axis == "row" else row
        grouped.setdefault(line, {})[position] = cell
        maximum = max(maximum, line)

    lines: list[str] = []
    for i in range(1, maximum + 1):
        line = grouped.get(i, {})
        lines.append(canon(dict(sorted(line.items()))))

    return lines


def _apply_to_sheet(sheet: Any, op: dict[str, Any]) -> Any:
    return apply_shared({"sheets": [sheet]}, op)["sheets"][0]


def _is_cell_form(sheet: Any) -> bool:
    """`array_diff(array_keys($sheet), CELL_FORM_KEYS) === []`, compared as strings."""
    return all(php_string_cast(key) in CELL_FORM_KEYS for key, _ in php_pairs(sheet))


def _without_name(sheet: Any) -> Any:
    if not isinstance(sheet, dict):
        return sheet
    return {key: item for key, item in sheet.items() if php_key(key) != "name"}


def _to_array(value: Any) -> Any:
    """PHP `(array) $value`."""
    if is_array(value):
        return value
    if value is None:
        return {}
    return {0: value}


def _unique_strings(keys: list[Any]) -> list[Any]:
    """`array_unique`, SORT_STRING: the first of each string form, order kept."""
    seen: set[str] = set()
    out: list[Any] = []
    for key in keys:
        text = php_string_cast(key)
        if text not in seen:
            seen.add(text)
            out.append(key)
    return out


def _row_major(addresses: list[Any]) -> list[str]:
    parsed: list[tuple[int, int, str]] = []

    for address in addresses:
        text = php_string_cast(address)
        cell = parse_address(text)
        if cell is not None:
            parsed.append((cell[1], cell[0], text))

    parsed.sort(key=lambda entry: (entry[0], entry[1]))

    return [text for _, _, text in parsed]

