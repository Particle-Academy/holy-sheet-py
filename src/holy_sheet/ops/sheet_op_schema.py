"""JSON Schema for one sheet op -- validate ops on the wire, or register the op
vocabulary as an LLM tool.

Mirrors PHP `Ops\\SheetOpSchema` (holy-sheet 2.3.3) key for key and in the same
key order; the parity suite compares the two documents.

`set_cell`, `set_range` and `set_workbook` are fancy-sheets' `SheetOp`
variants, same `type` and same fields, so a stored op stream can drive a live
`useSheetSync` session. One difference: `set_workbook.data` here is a Holy
Sheet schema, not fancy-sheets' `WorkbookData`.
"""

from __future__ import annotations

from typing import Any


class SheetOpSchema:
    """Namespace class, mirroring PHP's static `SheetOpSchema`."""

    #: Every op type, in the order the variants are listed.
    TYPES = (
        "set_cell", "set_range", "set_workbook",
        "clear_cell",
        "insert_rows", "delete_rows", "insert_columns", "delete_columns",
        "add_sheet", "remove_sheet", "rename_sheet", "move_sheet", "replace_sheet",
        "set_merged_regions", "set_column_widths", "set_frozen",
        "set_meta",
    )

    @staticmethod
    def json_schema() -> dict[str, Any]:
        """A fresh document on every call, so a caller editing it edits only its copy."""
        address = lambda: {"type": "string", "pattern": "^[A-Za-z]+[0-9]+$"}  # noqa: E731
        sheet = lambda: {"type": "string", "minLength": 1}  # noqa: E731
        count = lambda: {"type": "integer", "minimum": 1}  # noqa: E731
        position = lambda: {"type": "integer", "minimum": 1}  # noqa: E731
        obj = lambda: {"type": "object"}  # noqa: E731
        nullable_object = lambda: {"type": ["object", "null"]}  # noqa: E731
        value = lambda: {"type": ["string", "number", "boolean", "null"]}  # noqa: E731

        variants = [
            _variant(
                "set_cell",
                {"sheet": sheet(), "address": address(), "value": value(), "formula": {"type": "string"}, "computedValue": value(), "format": nullable_object(), "comment": nullable_object()},
                ["sheet", "address"],
                "Write one cell. Omitting formula or computedValue clears it; omitting format or comment keeps it; null clears it.",
            ),
            _variant(
                "set_range",
                {"sheet": sheet(), "start": address(), "end": address(), "values": {"type": "array", "items": {"type": "array", "items": value()}}},
                ["sheet", "start", "values"],
                "Write a block of values row-major from start, each as a set_cell with no formula.",
            ),
            _variant("set_workbook", {"data": obj()}, ["data"], "Replace the whole workbook schema."),
            _variant("clear_cell", {"sheet": sheet(), "address": address()}, ["sheet", "address"], "Remove one cell."),
            _variant("insert_rows", {"sheet": sheet(), "at": position(), "count": count()}, ["sheet", "at", "count"], "Insert rows before 1-based row `at`; cells, merges below move down."),
            _variant("delete_rows", {"sheet": sheet(), "at": position(), "count": count()}, ["sheet", "at", "count"], "Delete rows starting at 1-based row `at`; cells, merges below move up."),
            _variant("insert_columns", {"sheet": sheet(), "at": position(), "count": count()}, ["sheet", "at", "count"], "Insert columns before 1-based column `at` (A = 1); cells, merges and widths move right."),
            _variant("delete_columns", {"sheet": sheet(), "at": position(), "count": count()}, ["sheet", "at", "count"], "Delete columns starting at 1-based column `at`; cells, merges and widths move left."),
            _variant("add_sheet", {"index": {"type": "integer", "minimum": 0}, "sheet": obj()}, ["index", "sheet"], "Insert a sheet at a 0-based position."),
            _variant("remove_sheet", {"sheet": sheet()}, ["sheet"], "Remove a sheet by name."),
            _variant("rename_sheet", {"sheet": sheet(), "name": sheet()}, ["sheet", "name"], "Rename a sheet."),
            _variant("move_sheet", {"sheet": sheet(), "toIndex": {"type": "integer", "minimum": 0}}, ["sheet", "toIndex"], "Move a sheet to a 0-based position."),
            _variant("replace_sheet", {"sheet": sheet(), "data": obj()}, ["sheet", "data"], "Replace one sheet whole."),
            _variant(
                "set_merged_regions",
                {"sheet": sheet(), "mergedRegions": {"type": "array", "items": {"type": "object", "required": ["start", "end"], "properties": {"start": address(), "end": address()}}}},
                ["sheet", "mergedRegions"],
                "Set every merged region of a sheet.",
            ),
            # A LIST too (PHP 2.3.2, found by this port). PHP encodes a map whose
            # keys run 0..n-1 as a JSON list, so widths for columns A, B and C
            # arrive as `[120, 80, 140]` and no widths as `[]`; 2.3.1 allowed only
            # the empty list. A list's position is the column index.
            _variant(
                "set_column_widths",
                {
                    "sheet": sheet(),
                    "columnWidths": {
                        "type": ["object", "array"],
                        "items": {"type": "number", "minimum": 0},
                        "additionalProperties": {"type": "number", "minimum": 0},
                    },
                },
                ["sheet", "columnWidths"],
                "Set every column width of a sheet (0-based column index to pixels; a list is indexed by position); empty removes them.",
            ),
            _variant("set_frozen", {"sheet": sheet(), "rows": {"type": "integer", "minimum": 0}, "cols": {"type": "integer", "minimum": 0}}, ["sheet", "rows", "cols"], "Set frozen rows and columns."),
            _variant("set_meta", {"meta": nullable_object()}, ["meta"], "Replace the workbook meta, or remove it with null."),
        ]

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Holy Sheet op",
            "description": "One op from Agent::diff, applied by Agent::reduce.",
            "oneOf": variants,
        }


def _variant(op_type: str, properties: dict[str, Any], required: list[str], description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "required": ["type", *required],
        "additionalProperties": False,
        "properties": {"type": {"const": op_type}, **properties},
    }
