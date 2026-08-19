"""One `xl/worksheets/sheetN.xml` -> a `Sheet`."""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from ..helpers.php import php_round
from ..workbook.cell import Cell
from ..workbook.cell_comment import CellComment
from ..workbook.cell_format import CellFormat
from ..workbook.merged_region import MergedRegion
from ..workbook.sheet import Sheet
from .xml import attr, find, find_all, parse_xml_or_none, text_of

_INTEGER_TEXT = re.compile(r"^-?\d+$")


class WorksheetParser:
    @staticmethod
    def parse(
        worksheet_xml: bytes | str | None,
        name: str,
        styles_index: list[CellFormat | None],
        comments: dict[str, CellComment] | None = None,
        shared_strings: list[str] | None = None,
    ) -> Sheet:
        root = parse_xml_or_none(worksheet_xml)
        if root is None:
            return Sheet(name=name)

        return Sheet(
            name=name,
            cells=_parse_cells(root, styles_index, comments or {}, shared_strings or []),
            merged_regions=_parse_merges(root),
            column_widths=_parse_column_widths(root),
            **_parse_frozen(root),
        )


def _parse_cells(
    root: Element,
    styles_index: list[CellFormat | None],
    comments: dict[str, CellComment],
    shared_strings: list[str],
) -> dict[str, Cell]:
    cells: dict[str, Cell] = {}
    sheet_data = find(root, "sheetData")
    if sheet_data is None:
        return cells

    for row in find_all(sheet_data, "row"):
        for c in find_all(row, "c"):
            address = attr(c, "r") or ""
            if address == "":
                continue

            cell_type = attr(c, "t") or "n"
            style_attr = attr(c, "s")
            style_idx = int(style_attr) if style_attr is not None else 0
            fmt = styles_index[style_idx] if style_idx < len(styles_index) else None

            formula_el = find(c, "f")
            value_el = find(c, "v")
            formula: str | None = None
            cached_value: Any = None
            value: Any = None

            if formula_el is not None:
                formula = text_of(formula_el)
                cached_value = (
                    _coerce_value(text_of(value_el), cell_type)
                    if value_el is not None
                    else None
                )
            elif cell_type == "inlineStr":
                inline = find(c, "is")
                t = find(inline, "t") if inline is not None else None
                if t is not None:
                    value = text_of(t)
            elif cell_type == "s" and value_el is not None:
                idx = int(text_of(value_el) or 0)
                value = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
            elif value_el is not None:
                value = _coerce_value(text_of(value_el), cell_type)

            cells[address] = Cell(
                address=address,
                value=value,
                formula=formula,
                format=fmt,
                comment=comments.get(address),
                cached_value=cached_value,
            )
    return cells


def _coerce_value(raw: str, cell_type: str) -> Any:
    if cell_type == "b":
        return raw in ("1", "true", "TRUE")
    if cell_type in ("str", "inlineStr"):
        return raw
    if raw == "":
        return None
    if _INTEGER_TEXT.match(raw):
        return int(raw)
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_merges(root: Element) -> list[MergedRegion]:
    out: list[MergedRegion] = []
    for merge in find_all(find(root, "mergeCells"), "mergeCell"):
        ref = attr(merge, "ref") or ""
        if ":" in ref:
            start, end = ref.split(":", 1)
            out.append(MergedRegion(start, end))
    return out


def _parse_column_widths(root: Element) -> dict[int, float]:
    out: dict[int, float] = {}
    for col in find_all(find(root, "cols"), "col"):
        if attr(col, "customWidth") is None:
            continue
        col_min = int(attr(col, "min") or 1)
        col_max = int(attr(col, "max") or col_min)
        excel_width = float(attr(col, "width") or 0)
        # Inverse of the writer's (px - 5) / 7. php_round, not the builtin --
        # a width landing on a .5 pixel would otherwise round to even and come
        # back one pixel narrower than it went in.
        px = php_round(excel_width * 7 + 5)
        for i in range(col_min, col_max + 1):
            out[i - 1] = px
    return out


def _parse_frozen(root: Element) -> dict[str, int]:
    for view in find_all(find(root, "sheetViews"), "sheetView"):
        pane = find(view, "pane")
        if pane is None:
            continue
        return {
            "frozen_rows": int(attr(pane, "ySplit") or 0),
            "frozen_cols": int(attr(pane, "xSplit") or 0),
        }
    return {"frozen_rows": 0, "frozen_cols": 0}
