"""Rows (+ optional headers) -> a Holy Sheet schema with inferred column types.

The output is the same shape `write()` consumes -- no builder objects, no
special handoff::

    schema = holy_sheet.from_array(rows, ["Region", "Revenue"])
    holy_sheet.write(schema, path)

Headers can be omitted, in which case the first row is treated as the header
row. If that first row's cells are numeric, the caller probably forgot the
headers -- but this does not second-guess: pass `headers` explicitly when you
need certainty.
"""

from __future__ import annotations

from typing import Any

from ..schema.inference import Inference


class ArrayBuilder:
    @staticmethod
    def build(
        rows: list[list[Any]],
        headers: list[str] | None = None,
        sheet_name: str = "Sheet 1",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}

        if headers is None:
            if not rows:
                return _wrap([], [], sheet_name, options)
            headers = [str(value) for value in rows[0]]
            rows = list(rows[1:])

        columns = _infer_columns(rows, headers, options)
        return _wrap(columns, rows, sheet_name, options)


def _infer_columns(
    rows: list[list[Any]], headers: list[str], options: dict[str, Any]
) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for i, header_name in enumerate(headers):
        column_values = [row[i] if i < len(row) else None for row in rows]
        columns.append(Inference.detect(column_values, str(header_name), options))
    return columns


def _wrap(
    columns: list[dict[str, Any]],
    rows: list[list[Any]],
    sheet_name: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    sheet: dict[str, Any] = {"name": sheet_name, "columns": columns, "rows": rows}

    if options.get("theme") is not None:
        sheet["theme"] = options["theme"]
    if isinstance(options.get("totals"), dict):
        sheet["totals"] = options["totals"]
    if options.get("frozenRows") is not None:
        sheet["frozenRows"] = int(options["frozenRows"])
    if options.get("frozenCols") is not None:
        sheet["frozenCols"] = int(options["frozenCols"])

    return {"sheets": [sheet]}
