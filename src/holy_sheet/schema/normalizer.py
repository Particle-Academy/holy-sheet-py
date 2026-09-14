"""Loose input schema -> the canonical `Workbook` the writer walks.

Everything that makes the input agent-friendly is resolved here: two sheet
shapes (row-oriented and sparse `cells`), themes, column types, symbolic
totals, formula promotion, date serials, and PHP's numeric-string coercion. The
writer downstream is deliberately dumb.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..helpers.php import (
    entries,
    is_array,
    is_numeric_string,
    numeric_string_to_number,
)
from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
from ..workbook.cell_comment import CellComment
from ..workbook.cell_format import CellFormat
from ..workbook.merged_region import MergedRegion
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from ..writer.format.date_converter import DateConverter
from . import column_widths
from .theme import Theme

_AGG_FUNCTIONS = ("SUM", "AVG", "COUNT", "MIN", "MAX")


class Normalizer:
    def normalize(self, schema: Any) -> Workbook:
        sheets = [
            self._normalize_sheet(sheet) for _, sheet in entries(schema.get("sheets", []))
        ]
        return Workbook(sheets=sheets, meta=schema.get("meta") or {})

    def _normalize_sheet(self, sheet: dict[str, Any]) -> Sheet:
        name = str(sheet["name"])

        if sheet.get("cells") is not None:
            return Sheet(
                name=name,
                cells=self._normalize_cell_map(sheet["cells"]),
                merged_regions=self._normalize_merges(sheet.get("mergedRegions") or []),
                column_widths=self._normalize_column_widths(sheet.get("columnWidths") or {}),
                frozen_rows=int(sheet.get("frozenRows") or 0),
                frozen_cols=int(sheet.get("frozenCols") or 0),
            )

        cells: dict[str, Cell] = {}
        columns = sheet.get("columns") or []
        rows = sheet.get("rows") or []
        theme = Theme(sheet.get("theme") or "default")
        header_offset = 0

        column_formats: dict[int, CellFormat | None] = {}
        column_by_header: dict[str, int] = {}
        for col_idx, column_def in entries(columns):
            column_formats[col_idx] = self._column_format(column_def)
            if isinstance(column_def, dict) and column_def.get("header") is not None:
                column_by_header[str(column_def["header"])] = col_idx

        if len(columns) > 0:
            for col, column_def in entries(columns):
                address = CellAddress.letter(col) + "1"
                header = (
                    column_def.get("header", "")
                    if isinstance(column_def, dict)
                    else str(column_def)
                )
                cells[address] = Cell(
                    address=address, value=str(header), format=theme.header_format()
                )
            header_offset = 1

        for r, row in entries(rows):
            for c, value in entries(row):
                address = CellAddress.letter(c) + str(r + 1 + header_offset)
                column_format = column_formats.get(c)
                row_band = theme.data_format(r)
                if row_band is not None:
                    merged = column_format.merge_with(row_band) if column_format else row_band
                else:
                    merged = column_format
                cells[address] = self._build_cell(address, value, merged)

        totals = sheet.get("totals")
        if totals and is_array(totals) and len(rows) > 0 and len(columns) > 0:
            totals_row = len(rows) + 1 + header_offset
            totals_theme = theme.totals_format()
            label_address = CellAddress.letter(0) + str(totals_row)
            cells[label_address] = Cell(
                address=label_address, value="Total", format=totals_theme
            )

            for header_key, agg_op in entries(totals):
                if header_key not in column_by_header:
                    continue
                col_idx = column_by_header[header_key]
                col_letter = CellAddress.letter(col_idx)
                range_start = col_letter + str(header_offset + 1)
                range_end = col_letter + str(len(rows) + header_offset)
                func = str(agg_op).upper()
                if func not in _AGG_FUNCTIONS:
                    continue
                excel_func = "AVERAGE" if func == "AVG" else func
                address = col_letter + str(totals_row)
                column_format = column_formats.get(col_idx)
                combined = (
                    column_format.merge_with(totals_theme) if column_format else totals_theme
                )
                cells[address] = Cell(
                    address=address,
                    value=None,
                    formula=f"{excel_func}({range_start}:{range_end})",
                    format=combined,
                )

        return Sheet(
            name=name,
            cells=cells,
            merged_regions=self._normalize_merges(sheet.get("mergedRegions") or []),
            column_widths=self._normalize_column_widths(sheet.get("columnWidths") or {}),
            frozen_rows=int(sheet.get("frozenRows") or 0),
            frozen_cols=int(sheet.get("frozenCols") or 0),
        )

    def _normalize_cell_map(self, cell_map: Any) -> dict[str, Cell]:
        cells: dict[str, Cell] = {}
        for raw_address, cell_data in entries(cell_map):
            address = str(raw_address)

            # A BARE SCALAR cell, e.g. {"A1": 42} or {"A1": "=SUM(B1:B5)"}.
            # A leading "=" promotes to a formula; everything else is a plain
            # value. (The Node port is a minor behind here and has neither
            # bare scalars nor "="-promotion; PHP is the reference.)
            if not isinstance(cell_data, dict):
                bare, formula = self._promote_formula(cell_data)
                cells[address] = Cell(
                    address=address,
                    value=None if formula is not None else self._coerce_value(bare, None),
                    formula=formula,
                )
                continue

            fmt = (
                CellFormat.from_dict(cell_data["format"])
                if isinstance(cell_data.get("format"), dict)
                else None
            )
            comment = _comment_from(cell_data.get("comment"))

            cells[address] = Cell(
                address=address,
                value=self._coerce_value(cell_data.get("value"), fmt),
                formula=cell_data.get("formula"),
                format=fmt,
                comment=comment,
                cached_value=cell_data.get("computedValue"),
            )
        return cells

    def _normalize_merges(self, merges: Any) -> list[MergedRegion]:
        out: list[MergedRegion] = []
        for _, merge in entries(merges):
            if isinstance(merge, dict) and merge.get("start") is not None and merge.get("end") is not None:
                out.append(MergedRegion(str(merge["start"]), str(merge["end"])))
        return out

    def _normalize_column_widths(self, widths: Any) -> dict[int, float]:
        # JSON object keys arrive as strings ({"0": 120}); PHP's json_decode
        # turns numeric keys back into ints and Python does not, so the cast is
        # explicit here. Insertion order is preserved and IS observable -- it
        # decides the order of the `<col>` elements.
        #
        # An entry that is not a column index and a width is skipped. `int(key)`
        # raised ValueError for "abc" and took to_bytes() and diff() down with it
        # (PHP 2.3.4 skips it too).
        out: dict[int, float] = {}
        for key, px in entries(widths):
            index = column_widths.index(key)
            width = column_widths.width(px)
            if index is None or width is None:
                continue
            out[index] = width
        return out

    def _column_format(self, column_def: Any) -> CellFormat | None:
        if not isinstance(column_def, dict):
            return None
        column_type = column_def.get("type", "auto")
        decimals = None if column_def.get("decimals") is None else int(column_def["decimals"])
        currency = column_def.get("currency")

        if column_type == "integer":
            return CellFormat(display_format="number", decimals=0)
        if column_type == "number":
            return (
                CellFormat(display_format="number", decimals=decimals)
                if decimals is not None
                else None
            )
        if column_type == "percent":
            return CellFormat(
                display_format="percentage", decimals=1 if decimals is None else decimals
            )
        if column_type == "currency":
            return CellFormat(
                display_format="currency",
                decimals=2 if decimals is None else decimals,
                currency=currency,
            )
        if column_type == "date":
            return CellFormat(display_format="date")
        if column_type == "datetime":
            return CellFormat(display_format="datetime")
        return None

    def _build_cell(self, address: str, value: Any, column_format: CellFormat | None) -> Cell:
        if isinstance(value, dict):
            cell_format = (
                CellFormat.from_dict(value["format"])
                if isinstance(value.get("format"), dict)
                else None
            )
            merged = column_format.merge_with(cell_format) if column_format else cell_format
            comment = _comment_from(value.get("comment"))
            return Cell(
                address=address,
                value=self._coerce_value(value.get("value"), merged),
                formula=value.get("formula"),
                format=merged,
                comment=comment,
                cached_value=value.get("computedValue"),
            )

        value, formula = self._promote_formula(value)

        return Cell(
            address=address,
            value=None if formula is not None else self._coerce_value(value, column_format),
            formula=formula,
            format=column_format,
        )

    def _promote_formula(self, value: Any) -> tuple[Any, str | None]:
        """A bare `"=SUM(B2:B10)"` string is an Excel formula, not text.

        Only BARE strings promote. An object cell is always the caller's
        explicit intent, so `{"value": "=literal"}` is the escape hatch for a
        genuine leading-"=" string and `{"formula": "..."}` says the caller
        already knows. A lone `"="` is too short to be a formula and stays text.
        """
        if isinstance(value, str) and len(value) > 1 and value[0] == "=":
            return None, value[1:]
        return value, None

    def _coerce_value(self, value: Any, fmt: CellFormat | None) -> Any:
        if value is None:
            return None

        display = fmt.display_format if fmt else None
        if display in ("date", "datetime"):
            if isinstance(value, (datetime, date)) or (
                isinstance(value, str) and value.strip() != ""
            ):
                return DateConverter.to_serial(value, display == "datetime")

        # `bool` is checked implicitly by is_numeric_string, which rejects it --
        # otherwise `True` would be "numeric" and land in a numeric cell.
        if isinstance(value, str) and is_numeric_string(value):
            return numeric_string_to_number(value)

        if isinstance(value, (datetime, date)):
            return DateConverter.to_serial(value, True)

        if not isinstance(value, (str, int, float, bool)):
            return str(value)

        return value


def _comment_from(data: Any) -> CellComment | None:
    if not isinstance(data, dict):
        return None
    return CellComment(
        text=str(data.get("text", "")),
        author=data.get("author"),
        color=data.get("color"),
    )
