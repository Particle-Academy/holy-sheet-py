"""A read `Workbook` -> the Holy Sheet schema `describe()` returns.

Mirrors PHP `Reader\\WorkbookSchema`. Shared by every reader (xlsx, ods) so the
output shape cannot drift between formats: key order, which empty things are
dropped, and how a date-formatted serial becomes an ISO string are decided here
and only here.
"""

from __future__ import annotations

from typing import Any

from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
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
        # Only templates say so; a document read is unchanged.
        if workbook.source_kind == "template":
            schema["sourceKind"] = "template"
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
    if sheet.row_heights:
        # Whole pixels come back as integers, as PHP returns them.
        out["rowHeights"] = {k: int(v) if float(v).is_integer() else v for k, v in sheet.row_heights.items()}
    if sheet.print_setup is not None:
        print_out: dict[str, Any] = {}
        if sheet.print_setup["orientation"] is not None:
            print_out["orientation"] = sheet.print_setup["orientation"]
        if sheet.print_setup["fitToWidth"]:
            print_out["fitToWidth"] = True
        if sheet.print_setup["fitToPage"]:
            print_out["fitToPage"] = True
        if sheet.print_setup["footer"] is not None:
            print_out["footer"] = {k: v for k, v in sheet.print_setup["footer"].items() if v != ""}
        out["print"] = print_out
    if sheet.charts:
        out["charts"] = [_chart_to_schema(chart, sheet.name) for chart in sheet.charts]
    return out


def _chart_to_schema(chart: dict[str, Any], sheet_name: str) -> dict[str, Any]:
    """A normalised chart in the schema's own form. Mirrors PHP WorkbookSchema::chartToSchema."""
    out: dict[str, Any] = {"type": chart["type"]}
    if chart["title"] is not None:
        out["title"] = chart["title"]
    if chart["categories"] is not None:
        out["categories"] = chart["categories"].to_schema(sheet_name)
    series_out = []
    for s in chart["series"]:
        series: dict[str, Any] = {}
        if s["name"] is not None:
            series["name"] = s["name"]
        series["values"] = s["values"].to_schema(sheet_name)
        if s["color"] is not None:
            series["color"] = "#" + s["color"]
        if s.get("points"):
            series["points"] = [{"index": p["index"], "color": "#" + p["color"]} for p in s["points"]]
        series_out.append(series)
    out["series"] = series_out
    out["position"] = {
        "from": CellAddress.letter(chart["from"][0]) + str(chart["from"][1] + 1),
        "to": CellAddress.letter(chart["to"][0]) + str(chart["to"][1] + 1),
    }
    if chart["dataLabels"]:
        out["dataLabels"] = True
    if chart["legend"] is not None:
        out["legend"] = chart["legend"]
    if chart["format"] is not None:
        out["format"] = chart["format"]
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
