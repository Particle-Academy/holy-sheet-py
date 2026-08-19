"""Conservative, high-confidence schema repairs.

Only patterns where the intended schema is unambiguous:

* top-level singular `sheet` -> `sheets` (wrapped in a list if needed)
* sheet `row` -> `rows`
* an integer-keyed rows OBJECT -> an indexed list
* stringified numerics on number/integer/currency/percent columns
* an unknown theme -> `default`
* whitespace around sparse-cell A1 addresses
* a missing column type whose values all look like ISO dates -> date/datetime

Ambiguous cases are deliberately left alone: an agent should SEE those errors
and fix them, because auto-repair that guesses is how a schema bug becomes
permanent. `repairs` is a list of human-readable strings for exactly that
reason -- so the caller can log what was fixed and stop emitting it.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import is_array, is_list, is_numeric_string, numeric_string_to_number

_VALID_THEMES = ("default", "minimal", "plain", "business")
_ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$"
)
_INTEGER_KEY = re.compile(r"^\d+$")
_NUMERIC_COLUMN_TYPES = ("number", "integer", "currency", "percent")


class Repairer:
    def __init__(self) -> None:
        self._repairs: list[str] = []

    def repair(self, schema: Any) -> tuple[Any, list[str]]:
        self._repairs = []
        if not isinstance(schema, dict):
            return schema, []

        schema = self._repair_top_level(dict(schema))

        sheets = schema.get("sheets")
        if is_array(sheets):
            if isinstance(sheets, dict):
                schema["sheets"] = {
                    key: self._repair_sheet(sheet, f"sheets[{key}]")
                    for key, sheet in sheets.items()
                }
            else:
                schema["sheets"] = [
                    self._repair_sheet(sheet, f"sheets[{i}]")
                    for i, sheet in enumerate(sheets)
                ]

        return schema, self._repairs

    def _repair_top_level(self, schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("sheets") is None and schema.get("sheet") is not None:
            value = schema.pop("sheet")
            schema["sheets"] = value if is_list(value) else [value]
            self._repairs.append("renamed top-level 'sheet' -> 'sheets'")
        return schema

    def _repair_sheet(self, sheet: Any, path: str) -> Any:
        if not isinstance(sheet, dict):
            return sheet
        sheet = dict(sheet)

        if sheet.get("rows") is None and sheet.get("row") is not None:
            sheet["rows"] = sheet.pop("row")
            self._repairs.append(f"renamed '{path}.row' -> '{path}.rows'")

        rows = sheet.get("rows")
        if isinstance(rows, dict):
            all_integer_keys = all(
                isinstance(key, int)
                or (isinstance(key, str) and _INTEGER_KEY.match(key) is not None)
                for key in rows
            )
            if all_integer_keys:
                sheet["rows"] = list(rows.values())
                self._repairs.append(
                    f"converted '{path}.rows' from integer-keyed object to indexed list"
                )

        if sheet.get("theme") is not None and sheet["theme"] not in _VALID_THEMES:
            original = sheet["theme"]
            sheet["theme"] = "default"
            self._repairs.append(
                f"changed '{path}.theme' from '{original}' to 'default' (unknown theme)"
            )

        cells = sheet.get("cells")
        if isinstance(cells, dict):
            cleaned: dict[str, Any] = {}
            changed = False
            for address, data in cells.items():
                trimmed = address.strip() if isinstance(address, str) else str(address)
                if trimmed != str(address):
                    changed = True
                cleaned[trimmed] = data
            if changed:
                sheet["cells"] = cleaned
                self._repairs.append(
                    f"trimmed whitespace from cell addresses in '{path}.cells'"
                )

        if isinstance(sheet.get("columns"), list) and isinstance(sheet.get("rows"), list):
            sheet = self._repair_column_type_inference(sheet, path)
            sheet = self._repair_stringified_numerics(sheet)

        return sheet

    def _repair_column_type_inference(self, sheet: dict[str, Any], path: str) -> dict[str, Any]:
        columns = list(sheet["columns"])
        for col_idx, column in enumerate(columns):
            if not isinstance(column, dict):
                continue
            # Only fill in a type that was omitted entirely -- never override.
            if column.get("type") is not None and column["type"] != "auto":
                continue

            values = [
                (row_idx, row[col_idx])
                for row_idx, row in enumerate(sheet["rows"])
                if isinstance(row, list) and col_idx < len(row) and row[col_idx] is not None
            ]
            if not values:
                continue

            if not all(
                isinstance(value, str) and _ISO_DATE.match(value) is not None
                for _, value in values
            ):
                continue

            first = next((value for row_idx, value in values if row_idx == 0), "")
            inferred = "datetime" if "T" in first else "date"
            column = dict(column)
            column["type"] = inferred
            columns[col_idx] = column
            self._repairs.append(
                f"inferred '{path}.columns[{col_idx}].type' = '{inferred}' from row values"
            )
        sheet["columns"] = columns
        return sheet

    def _repair_stringified_numerics(self, sheet: dict[str, Any]) -> dict[str, Any]:
        rows = [list(row) if isinstance(row, list) else row for row in sheet["rows"]]
        for col_idx, column in enumerate(sheet["columns"]):
            if not isinstance(column, dict):
                continue
            column_type = column.get("type", "auto")
            if column_type not in _NUMERIC_COLUMN_TYPES:
                continue

            for row in rows:
                if not isinstance(row, list) or col_idx >= len(row):
                    continue
                value = row[col_idx]
                if isinstance(value, str) and is_numeric_string(value):
                    row[col_idx] = numeric_string_to_number(value)
        sheet["rows"] = rows
        # Deliberately NOT recorded in `repairs`: one note per coerced cell is
        # noise, and one per column is still noise. The change is a type
        # correction, not a behaviour change.
        return sheet
