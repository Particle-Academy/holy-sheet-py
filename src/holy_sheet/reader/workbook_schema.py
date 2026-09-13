"""A read `Workbook` -> the Holy Sheet schema `describe()` returns.

Mirrors PHP `Reader\\WorkbookSchema`. Shared by every reader (xlsx, ods) so the
output shape cannot drift between formats: key order, which empty things are
dropped, and how a date-formatted serial becomes an ISO string are decided here
and only here.
"""

from __future__ import annotations

from typing import Any

from ..workbook.cell import Cell
from ..workbook.cell_comment import CellComment
from ..workbook.cell_format import CellFormat
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from .format.date_inverter import DateInverter


class WorkbookSchema:
    @staticmethod
    def from_workbook(workbook: Workbook) -> dict[str, Any]:
        schema: dict[str, Any] = {"sheets": [_sheet_to_schema(sheet) for sheet in workbook.sheets]}
        if workbook.meta:
            schema["meta"] = workbook.meta
        return schema


def _sheet_to_schema(sheet: Sheet) -> dict[str, Any]:
    cells: dict[str, Any] = {}
    for address, cell in sheet.cells.items():
        cell_schema = _cell_to_schema(cell)
        if cell_schema is None:  # wholly-empty cell: absent means the same thing
            continue
        cells[address] = cell_schema

    out: dict[str, Any] = {"name": sheet.name, "cells": cells}
    if sheet.merged_regions:
        out["mergedRegions"] = [{"start": m.start, "end": m.end} for m in sheet.merged_regions]
    if sheet.column_widths:
        out["columnWidths"] = dict(sheet.column_widths)
    if sheet.frozen_rows > 0:
        out["frozenRows"] = sheet.frozen_rows
    if sheet.frozen_cols > 0:
        out["frozenCols"] = sheet.frozen_cols
    return out


def _cell_to_schema(cell: Cell) -> dict[str, Any] | None:
    value = cell.value
    fmt = cell.format

    # A date-formatted serial goes back out as the ISO string it came in as,
    # so the described schema can be written again without drifting a day
    # further from the epoch every round trip.
    if (
        fmt is not None
        and fmt.display_format in ("date", "datetime")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        value = DateInverter.to_iso(float(value), fmt.display_format == "datetime")

    out: dict[str, Any] = {"value": value}
    if cell.formula is not None:
        out["formula"] = cell.formula
    if cell.cached_value is not None:
        out["computedValue"] = cell.cached_value
    if fmt is not None and not fmt.is_empty():
        out["format"] = _format_to_dict(fmt)
    if cell.comment is not None:
        out["comment"] = _comment_to_dict(cell.comment)

    if out == {"value": None}:
        return None
    return out


def _format_to_dict(fmt: CellFormat) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if fmt.bold:
        out["bold"] = True
    if fmt.italic:
        out["italic"] = True
    if fmt.text_align is not None:
        out["textAlign"] = fmt.text_align
    if fmt.display_format is not None:
        out["displayFormat"] = fmt.display_format
    if fmt.decimals is not None:
        out["decimals"] = fmt.decimals
    if fmt.color is not None:
        out["color"] = fmt.color
    if fmt.background_color is not None:
        out["backgroundColor"] = fmt.background_color
    if fmt.font_size is not None:
        out["fontSize"] = fmt.font_size
    if fmt.border_top is not None:
        out["borderTop"] = fmt.border_top
    if fmt.border_right is not None:
        out["borderRight"] = fmt.border_right
    if fmt.border_bottom is not None:
        out["borderBottom"] = fmt.border_bottom
    if fmt.border_left is not None:
        out["borderLeft"] = fmt.border_left
    if fmt.currency is not None:
        out["currency"] = fmt.currency
    return out


def _comment_to_dict(comment: CellComment) -> dict[str, Any]:
    out: dict[str, Any] = {"text": comment.text}
    if comment.author is not None:
        out["author"] = comment.author
    if comment.color is not None:
        out["color"] = comment.color
    return out
