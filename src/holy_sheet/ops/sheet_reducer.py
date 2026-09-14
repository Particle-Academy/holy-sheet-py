"""Apply sheet ops to a Holy Sheet schema, returning a new schema.

Mirrors PHP `Ops\\SheetReducer` (holy-sheet 2.3.3), which is normative: the same
op on the same schema gives the same schema in both runtimes, and
`tests/test_sheet_ops_parity_php.py` checks it against PHP.

Pure: the input schema and the ops are never modified, and the result shares
no mutable structure with either. Internally every level an op changes is
copied on the way down (PHP's copy-on-write, by hand) and the public entry
points deep-copy the result once.

An op naming a sheet or cell that is not there is skipped, as fancy-sheets'
`reduceWorkbook` skips an unknown sheet, so a replayed history degrades rather
than throws.

`set_cell`, `set_range` and `set_workbook` keep fancy-sheets' shapes and
semantics -- a `set_cell` without `formula` clears the formula and keeps the
format and comment; a null write to an absent cell does nothing -- so the same
ops can drive a live `useSheetSync` session. Everything else is holy-sheet's:
the structure fancy-sheets has no op for (sheets, rows, columns, merges, widths,
panes, meta) and the cell parts it does not carry (`computedValue`, `format`,
`comment`).

The row and column ops move cells, merged regions and column widths. They do
not rewrite formula text: a formula that should follow an inserted row is a
change to that cell, and a diff records it as one.

## `columnWidths` keys

JSON object keys arrive as strings (`{"0": 120}`); PHP's `json_decode` makes
them integers and Python's does not. `insert_columns` / `delete_columns` do what
PHP does, `(int) $key`, and write the moved widths back with INTEGER keys in
ascending order -- the same keys `describe()` returns. A key that is not a
column index (not an int key, and not a string of digits) is dropped, as in PHP
2.3.2: `"abc"` and `"1.5"` go, `"007"` is column 7. `set_column_widths` stores
the op's map as given, string keys and all. Comparisons (`SheetDiff.same`) treat
`"0"` and `0` as one key, as a PHP array does.

## Positions and counts

`index`, `toIndex`, `rows`, `cols`, `at` and `count` are read with
:func:`php_integer` (PHP 2.3.3): an int or a string of digits. A PRESENT value
that is neither, null included, skips the op; an absent one keeps its default.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ..workbook.cell_address import CellAddress
from ._php_array import (
    ascii_upper,
    get,
    has,
    is_array,
    is_index_key,
    php_int_cast,
    php_integer,
    php_key,
    php_pairs,
    php_string_cast,
    php_trim,
    parse_address,
    values,
    writable,
)
from .sheet_op_schema import SheetOpSchema

#: Sheet keys the granular ops address. A sheet with any other key is authored form.
CELL_FORM_KEYS = ("name", "cells", "mergedRegions", "columnWidths", "frozenRows", "frozenCols")

#: Op fields that hold a position or a count (PHP 2.3.3's guard, in its order).
_POSITION_FIELDS = ("index", "toIndex", "rows", "cols", "at", "count")


class SheetReducer:
    """Namespace class, mirroring PHP's static `SheetReducer`."""

    CELL_FORM_KEYS = CELL_FORM_KEYS

    @staticmethod
    def integer(value: Any) -> int | None:
        """PHP `SheetReducer::integer()`: an int, or a string of digits; anything else is None."""
        return php_integer(value)

    @staticmethod
    def apply_all(schema: Any, ops: Any) -> Any:
        """Apply every op in order; returns a new schema."""
        _require_array(schema, "schema")
        _require_array(ops, "ops")
        return copy.deepcopy(apply_all_shared(schema, values(ops)))

    @staticmethod
    def apply(schema: Any, op: Any) -> Any:
        """Apply one op; returns a new schema."""
        _require_array(schema, "schema")
        return copy.deepcopy(apply_shared(schema, op))


def apply_all_shared(schema: Any, ops: list[Any]) -> Any:
    """`applyAll`, without the final copy. The result may share structure with
    the inputs; never mutate it. For the diff, which replays ops many times."""
    for op in ops:
        schema = apply_shared(schema, op)
    return schema


def apply_shared(schema: Any, op: Any) -> Any:
    """`apply`, without the final copy (see :func:`apply_all_shared`)."""
    _require_array(op, "op")
    op_type = get(op, "type")

    # A string, compared strictly (PHP 2.3.2). PHP's `switch` compared loosely,
    # so `type: true` matched the first case and REMOVED a sheet.
    if not isinstance(op_type, str) or op_type not in SheetOpSchema.TYPES:
        return schema

    if _strict(op_type, "set_workbook"):
        data = get(op, "data")
        return data if is_array(data) else schema

    if _strict(op_type, "set_meta"):
        out = writable(schema)
        meta = get(op, "meta")
        if meta is None:
            out.pop("meta", None)
        else:
            out["meta"] = meta
        return out

    raw_sheets = get(schema, "sheets")
    sheets = values(raw_sheets) if is_array(raw_sheets) else []

    if _strict(op_type, "add_sheet"):
        sheet = get(op, "sheet")
        if not is_array(sheet):
            return schema
        index = php_integer(op["index"]) if has(op, "index") else len(sheets)
        if index is None:
            return schema
        sheets.insert(max(0, min(len(sheets), index)), sheet)
        out = writable(schema)
        out["sheets"] = sheets
        return out

    at = _find(sheets, php_string_cast(get(op, "sheet", "")))
    if at is None:
        return schema

    # Positions and counts are ints or digit strings (PHP 2.3.3). A present value
    # that is neither skips the op: `(int)` read junk as 0, which moved a sheet to
    # the front, inserted one there, or unfroze panes.
    for field in _POSITION_FIELDS:
        if has(op, field) and php_integer(op[field]) is None:
            return schema

    case = op_type

    if case == "remove_sheet":
        del sheets[at]
    elif case == "rename_sheet":
        renamed = dict(sheets[at])
        renamed["name"] = php_string_cast(get(op, "name", sheets[at]["name"]))
        sheets[at] = renamed
    elif case == "move_sheet":
        moved = sheets.pop(at)
        to = max(0, min(len(sheets), _position(op, "toIndex", at)))
        sheets.insert(to, moved)
    elif case == "replace_sheet":
        data = get(op, "data")
        if is_array(data):
            sheets[at] = data
    elif case == "set_merged_regions":
        sheets[at] = _set_or_unset_array(sheets[at], "mergedRegions", get(op, "mergedRegions", []))
    elif case == "set_column_widths":
        sheets[at] = _set_or_unset_array(sheets[at], "columnWidths", get(op, "columnWidths", []))
    elif case == "set_frozen":
        sheets[at] = _set_or_unset_int(sheets[at], "frozenRows", _position(op, "rows", 0))
        sheets[at] = _set_or_unset_int(sheets[at], "frozenCols", _position(op, "cols", 0))
    elif case == "set_cell":
        sheets[at] = _set_cell(sheets[at], op)
    elif case == "set_range":
        sheets[at] = _set_range(sheets[at], op)
    elif case == "clear_cell":
        sheets[at] = _clear_cell(sheets[at], op)
    elif case == "insert_rows":
        sheets[at] = _shift_rows(sheets[at], _position(op, "at", 0), max(0, _position(op, "count", 0)))
    elif case == "delete_rows":
        sheets[at] = _shift_rows(sheets[at], _position(op, "at", 0), -max(0, _position(op, "count", 0)))
    elif case == "insert_columns":
        sheets[at] = _shift_columns(sheets[at], _position(op, "at", 0), max(0, _position(op, "count", 0)))
    elif case == "delete_columns":
        sheets[at] = _shift_columns(sheets[at], _position(op, "at", 0), -max(0, _position(op, "count", 0)))
    else:
        return schema

    out = writable(schema)
    out["sheets"] = sheets
    return out


def _require_array(value: Any, name: str) -> None:
    # PHP's signatures are `array $schema` / `array $op`: anything else is a TypeError there too.
    if not is_array(value):
        raise TypeError(f"[holy-sheet] {name} must be an array (dict or list), got {type(value).__name__}")


def _strict(value: Any, expected: str) -> bool:
    """`$value === 'string'`."""
    return isinstance(value, str) and value == expected


def _position(op: Any, key: str, default: int) -> int:
    """`self::integer($op[$key] ?? $default) ?? $default`, for a field the guard has checked."""
    integer = php_integer(get(op, key, default))
    return default if integer is None else integer


def _find(sheets: list[Any], name: str) -> int | None:
    for index, sheet in enumerate(sheets):
        if _strict(get(sheet, "name"), name):
            return index
    return None


def _set_or_unset_array(sheet: Any, key: str, value: Any) -> dict[Any, Any]:
    """`setOrUnset($sheet, $key, $value, [])`: an empty array or null removes the key."""
    out = writable(sheet)
    if value is None or (is_array(value) and len(value) == 0):
        out.pop(key, None)
    else:
        out[key] = value
    return out


def _set_or_unset_int(sheet: Any, key: str, value: int) -> dict[Any, Any]:
    """`setOrUnset($sheet, $key, (int) ..., 0)`."""
    out = writable(sheet)
    if value == 0:
        out.pop(key, None)
    else:
        out[key] = value
    return out


def _set_cell(sheet: Any, op: Any) -> Any:
    """fancy-sheets' `set_cell`, plus the parts of a cell it does not carry.

    - `value` is written; `formula` and `computedValue` are REPLACED -- absent
      in the op means absent in the cell, as fancy-sheets clears `formula`.
    - `format` and `comment` are KEPT when the op omits them, replaced when it
      carries one, and removed when it carries null.
    - A null write to an absent cell, carrying nothing else, does nothing.
    - An op without `value` writes a cell without `value`, and a cell left with
      no keys at all is removed.

    The address is trimmed (PHP `trim()`'s set) and upper-cased before it is
    validated and used as the key, as in PHP 2.3.2: `" a1 "` is A1.
    """
    address = ascii_upper(php_trim(php_string_cast(get(op, "address", ""))))

    if parse_address(address) is None:
        return sheet

    raw_cells = get(sheet, "cells")
    cells = raw_cells if is_array(raw_cells) else []
    existing = get(cells, address)
    value = get(op, "value")

    def carries(key: str) -> bool:
        return has(op, key) and op[key] is not None

    if (
        existing is None
        and value is None
        and not carries("formula")
        and not carries("computedValue")
        and not carries("format")
        and not carries("comment")
    ):
        return sheet

    # `value` is optional in a CellData ({"formula": "SUM(A1:A3)"} is a whole
    # cell), so an op without one writes a cell without one.
    cell: dict[str, Any] = {"value": value} if has(op, "value") else {}

    for key in ("formula", "computedValue"):
        if carries(key):
            cell[key] = op[key]

    for key in ("format", "comment"):
        if has(op, key):
            if op[key] is not None:
                cell[key] = op[key]
        elif is_array(existing) and has(existing, key):
            cell[key] = existing[key]

    out_cells = writable(cells)
    if not cell:
        out_cells.pop(address, None)
    else:
        out_cells[address] = cell

    out = writable(sheet)
    out["cells"] = out_cells
    return out


def _set_range(sheet: Any, op: Any) -> Any:
    """fancy-sheets' `set_range`: values row-major from `start`, each written as a
    `set_cell` with no formula. `end` is accepted and, as there, not read."""
    start = parse_address(php_string_cast(get(op, "start", "")))
    rows = get(op, "values")

    if start is None or not is_array(rows):
        return sheet

    col0, row0 = start

    for r, row in enumerate(values(rows)):
        for c, value in enumerate(values(row) if is_array(row) else []):
            sheet = _set_cell(sheet, {"address": CellAddress.letter(col0 + c) + str(row0 + r), "value": value})

    return sheet


def _clear_cell(sheet: Any, op: Any) -> Any:
    """`unset($sheet['cells'][strtoupper(trim((string) $op['address']))])`.

    No address validation, as in PHP. PHP's `unset` does not create a missing
    `cells`, is silent on a null one, and throws on a scalar one; so does this.
    """
    if not isinstance(sheet, dict) or "cells" not in sheet:
        return sheet

    cells = sheet["cells"]
    key = php_key(ascii_upper(php_trim(php_string_cast(get(op, "address", "")))))

    if cells is None:
        return sheet
    if isinstance(cells, str):
        raise TypeError("Cannot unset string offsets")
    if not is_array(cells):
        raise TypeError("Cannot unset offset in a non-array variable")

    # Keys compare as PHP stores them: "7" and 7 are one key. The caller's own
    # spelling of every other key is kept.
    remaining = {
        original: cell for original, cell in (cells.items() if isinstance(cells, dict) else enumerate(cells))
        if php_key(original) != key
    }
    if len(remaining) == len(cells):
        return sheet

    out = writable(sheet)
    out["cells"] = remaining
    return out


def _shift_rows(sheet: Any, at: int, delta: int) -> Any:
    """Insert (`delta` > 0) or delete (`delta` < 0) rows at 1-based row `at`."""
    if at < 1 or delta == 0:
        return sheet

    def move(col: int, row: int) -> tuple[int, int] | None:
        if row < at:
            return col, row
        if delta < 0 and row < at - delta:
            return None
        return col, row + delta

    sheet = _remap_cells(sheet, move)

    return _remap_merges(sheet, "row", at, delta)


def _shift_columns(sheet: Any, at: int, delta: int) -> Any:
    """Insert or delete columns at 1-based column `at` (A = 1)."""
    if at < 1 or delta == 0:
        return sheet

    def move(col: int, row: int) -> tuple[int, int] | None:
        number = col + 1
        if number < at:
            return col, row
        if delta < 0 and number < at - delta:
            return None
        return col + delta, row

    sheet = _remap_cells(sheet, move)

    raw_widths = get(sheet, "columnWidths")
    if is_array(raw_widths):
        widths: dict[int, Any] = {}
        for index, width in php_pairs(raw_widths):
            # A key that is not a column index is dropped (PHP 2.3.2), not read as
            # column 0: `(int) "abc"` would overwrite column A's width.
            if not is_index_key(index):
                continue
            column = php_int_cast(index)
            number = column + 1
            if number < at:
                widths[column] = width
            elif delta > 0 or number >= at - delta:
                widths[column + delta] = width
        # ksort: integer keys, ascending.
        sheet = _set_or_unset_array(sheet, "columnWidths", dict(sorted(widths.items())))

    return _remap_merges(sheet, "col", at, delta)


def _shift_span(start: int, end: int, at: int, delta: int) -> tuple[int, int] | None:
    """Move a 1-based span [start, end] for an insert or delete at `at`. None
    when a delete removes the whole span; a span the delete cuts into shrinks."""
    if delta > 0:
        return (start + delta if start >= at else start, end + delta if end >= at else end)

    last = at - delta - 1  # the last deleted index
    new_start = start if start < at else (start + delta if start > last else at)
    new_end = end if end < at else (end + delta if end > last else at - 1)

    return None if new_end < new_start else (new_start, new_end)


def _remap_cells(sheet: Any, move: Callable[[int, int], tuple[int, int] | None]) -> Any:
    raw_cells = get(sheet, "cells")
    if not is_array(raw_cells):
        return sheet

    placed: list[tuple[int, int, str, Any]] = []

    for address, cell in php_pairs(raw_cells):
        parsed = parse_address(php_string_cast(address))

        # A key that is not an address is dropped, as in PHP.
        if parsed is None:
            continue

        to = move(parsed[0], parsed[1])

        if to is not None:
            placed.append((to[1], to[0], CellAddress.letter(to[0]) + str(to[1]), cell))

    # Row-major, the order describe() reads cells in. Stable, like PHP 8's usort.
    placed.sort(key=lambda entry: (entry[0], entry[1]))

    cells: dict[str, Any] = {}
    for _, _, address, cell in placed:
        cells[address] = cell

    out = writable(sheet)
    out["cells"] = cells
    return out


def _remap_merges(sheet: Any, axis: str, at: int, delta: int) -> Any:
    raw_regions = get(sheet, "mergedRegions")
    if not is_array(raw_regions):
        return sheet

    regions: list[Any] = []

    for region in values(raw_regions):
        start = parse_address(php_string_cast(get(region, "start", "")))
        end = parse_address(php_string_cast(get(region, "end", "")))

        if start is None or end is None:
            regions.append(region)
            continue

        if axis == "row":
            moved = _shift_span(start[1], end[1], at, delta)
            if moved is None:
                continue
            regions.append({
                "start": CellAddress.letter(start[0]) + str(moved[0]),
                "end": CellAddress.letter(end[0]) + str(moved[1]),
            })
        else:
            moved = _shift_span(start[0] + 1, end[0] + 1, at, delta)
            if moved is None:
                continue
            regions.append({
                "start": CellAddress.letter(moved[0] - 1) + str(start[1]),
                "end": CellAddress.letter(moved[1] - 1) + str(end[1]),
            })

    return _set_or_unset_array(sheet, "mergedRegions", regions)
