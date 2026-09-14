"""Schema validation, before any xlsx writing happens.

Returns a list of STRUCTURED errors -- each with `path`, `expected`, `got`,
`value`, `hint` -- so an agent can recover without parsing a traceback. Empty
list means valid.

Hand-rolled on purpose. The package has zero runtime dependencies, a JSON Schema
validator would add several transitive ones, and the shape is small enough that
explicit checks read better than a declarative document would. (The JSON Schema
still exists -- see `tool_definition()` -- but it is the LLM's tool contract,
not this module's engine.)
"""

from __future__ import annotations

from typing import Any

from ..exceptions import SchemaException
from ..helpers.php import entries, is_array, is_list, type_of
from . import column_widths
from .repairer import Repairer

_ALLOWED_COLUMN_TYPES = [
    "auto",
    "string",
    "number",
    "integer",
    "boolean",
    "date",
    "datetime",
    "currency",
    "percent",
    "formula",
]

_ALLOWED_THEMES = ["default", "minimal", "plain", "business"]


class Validator:
    def validate(self, schema: Any) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []

        sheets = _get(schema, "sheets")
        if not is_array(sheets):
            errors.append(
                _error(
                    "sheets",
                    "array",
                    type_of(sheets),
                    sheets,
                    'Top-level "sheets" must be an array of sheet definitions.',
                )
            )
            return errors

        if len(sheets) == 0:
            errors.append(
                _error(
                    "sheets",
                    "non-empty array",
                    "empty array",
                    [],
                    "A workbook must contain at least one sheet.",
                )
            )
            return errors

        for key, sheet in entries(sheets):
            errors.extend(self._validate_sheet(sheet, f"sheets[{key}]"))

        return errors

    def assert_valid(self, schema: Any) -> None:
        """Raise `SchemaException` when validation fails.

        Named `assert_valid` rather than `assert` because `assert` is a Python
        keyword; the peers call it `assert()`.
        """
        errors = self.validate(schema)
        if errors:
            raise SchemaException.from_errors(errors)

    def validate_and_repair(self, schema: Any) -> dict[str, Any]:
        """Repair conservatively, then validate what came out.

        The error list describes the REPAIRED schema, so anything still in it is
        something the repairer deliberately declined to guess at.
        """
        repaired, repairs = Repairer().repair(schema)
        return {
            "schema": repaired,
            "errors": self.validate(repaired),
            "repairs": repairs,
        }

    # ------------------------------------------------------------------ #

    def _validate_sheet(self, sheet: Any, path: str) -> list[dict[str, Any]]:
        if not is_array(sheet):
            return [
                _error(
                    path,
                    "object",
                    type_of(sheet),
                    sheet,
                    'Each sheet must be an object with at least a "name" key.',
                )
            ]

        errors: list[dict[str, Any]] = []

        name = _get(sheet, "name")
        if not isinstance(name, str) or name.strip() == "":
            errors.append(
                _error(
                    f"{path}.name",
                    "non-empty string",
                    type_of(name),
                    name,
                    "Sheet name is required and visible in Excel's tab strip.",
                )
            )

        has_columns_rows = _has(sheet, "rows") or _has(sheet, "columns")
        has_cells = _has(sheet, "cells")

        if not has_columns_rows and not has_cells:
            errors.append(
                _error(
                    path,
                    "object with rows OR cells",
                    "object without either",
                    sheet,
                    "A sheet needs either {columns, rows} (row-oriented) or "
                    "{cells} (sparse A1-keyed) data.",
                )
            )

        if _has(sheet, "columns"):
            columns = _get(sheet, "columns")
            if not is_array(columns):
                errors.append(
                    _error(
                        f"{path}.columns",
                        "array",
                        type_of(columns),
                        columns,
                        "Columns must be an array of column definitions.",
                    )
                )
            else:
                for key, column in entries(columns):
                    errors.extend(self._validate_column(column, f"{path}.columns[{key}]"))

        if _has(sheet, "rows"):
            rows = _get(sheet, "rows")
            if not is_array(rows):
                errors.append(
                    _error(
                        f"{path}.rows",
                        "array",
                        type_of(rows),
                        rows,
                        "Rows must be an array of arrays.",
                    )
                )
            else:
                for key, row in entries(rows):
                    if not is_array(row):
                        errors.append(
                            _error(
                                f"{path}.rows[{key}]",
                                "array",
                                type_of(row),
                                row,
                                "Each row is an array of cell values, in column order.",
                            )
                        )

        if _has(sheet, "cells"):
            cells = _get(sheet, "cells")
            # `[]` is an empty map in PHP terms: PHP's describe() reports a sheet
            # with no cells that way, so rejecting it broke describe() -> write().
            if not is_array(cells) or (is_list(cells) and len(cells) > 0):
                errors.append(
                    _error(
                        f"{path}.cells",
                        "object keyed by A1 address",
                        type_of(cells),
                        cells,
                        'Cells must be an object/map keyed by A1 references like "A1", "B2".',
                    )
                )

        if _has(sheet, "theme") and _get(sheet, "theme") not in _ALLOWED_THEMES:
            errors.append(
                _error(
                    f"{path}.theme",
                    "one of: default, minimal, plain, business",
                    "unknown",
                    _get(sheet, "theme"),
                    "Pick a built-in theme or omit for default.",
                )
            )

        widths = _get(sheet, "columnWidths")
        if widths is not None:
            if not is_array(widths):
                errors.append(
                    _error(
                        f"{path}.columnWidths",
                        "object keyed by 0-based column index",
                        type_of(widths),
                        widths,
                        'Column widths map a 0-based column index to pixels: {"0": 120, "1": 80}.',
                    )
                )
            else:
                for key, px in entries(widths):
                    if column_widths.index(key) is None:
                        errors.append(
                            _error(
                                f"{path}.columnWidths.{key}",
                                f"a 0-based column index from 0 to {column_widths.MAX_INDEX}",
                                type_of(key),
                                key,
                                'Keys are 0-based column indexes: "0" is column A, "1" is column B. Use the index, not the letter.',
                            )
                        )
                    elif column_widths.width(px) is None:
                        errors.append(
                            _error(
                                f"{path}.columnWidths.{key}",
                                "a non-negative number of pixels",
                                type_of(px),
                                px,
                                "A width is a number of pixels, like 120.",
                            )
                        )

        return errors

    def _validate_column(self, column: Any, path: str) -> list[dict[str, Any]]:
        if not is_array(column):
            return [
                _error(
                    path,
                    "object",
                    type_of(column),
                    column,
                    'Each column is an object with at least a "header" key.',
                )
            ]

        errors: list[dict[str, Any]] = []
        header = _get(column, "header")
        if not isinstance(header, str):
            errors.append(
                _error(
                    f"{path}.header",
                    "string",
                    type_of(header),
                    header,
                    "Column header is the visible label in row 1.",
                )
            )

        if _has(column, "type") and _get(column, "type") not in _ALLOWED_COLUMN_TYPES:
            errors.append(
                _error(
                    f"{path}.type",
                    "one of: " + ", ".join(_ALLOWED_COLUMN_TYPES),
                    "unknown",
                    _get(column, "type"),
                    'Pick a supported type or omit for "auto" (inferred per cell).',
                )
            )

        return errors


def _error(path: str, expected: str, got: str, value: Any, hint: str) -> dict[str, Any]:
    return {"path": path, "expected": expected, "got": got, "value": value, "hint": hint}


def _get(container: Any, key: str) -> Any:
    return container.get(key) if isinstance(container, dict) else None


def _has(container: Any, key: str) -> bool:
    """PHP's `isset()`: a key present with a null value counts as absent."""
    return isinstance(container, dict) and container.get(key) is not None
