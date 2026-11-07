"""A read sheet as rows. A port of PHP `Reader\\RowsView`.

A list of rows, each a list of values in column order, row 1 and column A at
index 0. A blank row in the middle is ``[]``, so ``rows[n - 1]`` is always row
n; trailing blank rows are not padded, and a row stops at its last value. A
formula gives its cached ``computedValue`` (None when the file cached none)
unless ``formulas="formula"``, which gives the formula with its leading ``=``.
A merged region's value is at its top-left cell only. The view is sized before
it is built and refuses past ``max_cells`` positions.
"""

from __future__ import annotations

from typing import Any

from ..exceptions import ReadLimitExceeded
from .read_limits import DEFAULT_READ_LIMITS, address


def rows_view(sheet: dict[str, Any], formulas: str = "computed", max_cells: int | None = None) -> list[list[Any]]:
    if formulas not in ("computed", "formula"):
        raise ValueError('[holy-sheet] rows option formulas must be "computed" or "formula"')
    if max_cells is None:
        max_cells = DEFAULT_READ_LIMITS["maxCells"]
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells < 1:
        raise ValueError("[holy-sheet] rows option max_cells must be a positive integer")

    covered = _covered(sheet.get("mergedRegions"))
    values: dict[int, dict[int, Any]] = {}
    cells = sheet.get("cells")
    if isinstance(cells, dict):
        for key, cell in cells.items():
            at = address(str(key))
            if at is None:
                continue
            col, row = at
            if row < 1 or _is_covered(covered, col, row):
                continue
            value = _value(cell, formulas)
            if value is None:
                continue
            values.setdefault(row, {})[col] = value
    if not values:
        return []

    size = sum(max(by_col) + 1 for by_col in values.values())
    if size > max_cells:
        raise ReadLimitExceeded("maxCells", max_cells, size, "the rows view would hold")

    out: list[list[Any]] = []
    for r in range(1, max(values) + 1):
        by_col = values.get(r)
        if by_col is None:
            out.append([])
            continue
        line: list[Any] = [None] * (max(by_col) + 1)
        for c, v in by_col.items():
            line[c] = v
        out.append(line)
    return out


def _value(cell: Any, mode: str) -> Any:
    if not isinstance(cell, dict):
        # A bare scalar; a bare "=..." string is a formula, as the writer reads it.
        if isinstance(cell, str) and len(cell) > 1 and cell[0] == "=":
            return cell if mode == "formula" else None
        return cell
    formula = cell.get("formula")
    if isinstance(formula, str) and formula != "":
        if mode == "formula":
            return formula if formula[0] == "=" else "=" + formula
        return cell.get("computedValue")
    return cell.get("value")


def _covered(regions: Any) -> list[tuple[int, int, int, int]]:
    out: list[tuple[int, int, int, int]] = []
    for m in regions if isinstance(regions, list) else []:
        a = address(m["start"]) if isinstance(m, dict) and isinstance(m.get("start"), str) else None
        b = address(m["end"]) if isinstance(m, dict) and isinstance(m.get("end"), str) else None
        if a is not None and b is not None:
            out.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
    return out


def _is_covered(covered: list[tuple[int, int, int, int]], col: int, row: int) -> bool:
    for c1, r1, c2, r2 in covered:
        if c1 <= col <= c2 and r1 <= row <= r2 and (col != c1 or row != r1):
            return True
    return False
