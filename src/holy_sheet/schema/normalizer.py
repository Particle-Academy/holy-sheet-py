"""Loose input schema -> the canonical `Workbook` the writer walks.

Everything that makes the input agent-friendly is resolved here: two sheet
shapes (row-oriented and sparse `cells`), themes, column types, symbolic
totals, formula promotion, date serials, and PHP's numeric-string coercion. The
writer downstream is deliberately dumb.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..exceptions import SchemaException
from ..helpers.php import (
    entries,
    is_array,
    is_list,
    is_numeric_string,
    numeric_string_to_number,
    type_of,
)
from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
from ..workbook.chart_range import ChartRange
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
            sparse = self._normalize_cell_map(sheet["cells"], name)
            return Sheet(
                name=name,
                cells=sparse,
                merged_regions=self._normalize_merges(sheet.get("mergedRegions") or []),
                column_widths=self._normalize_widths(sheet),
                frozen_rows=int(sheet.get("frozenRows") or 0),
                frozen_cols=int(sheet.get("frozenCols") or 0),
                row_heights=normalize_row_heights(sheet.get("rowHeights")),
                print_setup=normalize_print(sheet.get("print")),
                charts=normalize_charts(sheet.get("charts"), name, sparse),
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
                cells[address] = self._build_cell(address, value, merged, name)

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
            column_widths=self._normalize_widths(sheet),
            frozen_rows=int(sheet.get("frozenRows") or 0),
            frozen_cols=int(sheet.get("frozenCols") or 0),
            row_heights=normalize_row_heights(sheet.get("rowHeights")),
            print_setup=normalize_print(sheet.get("print")),
            charts=normalize_charts(sheet.get("charts"), name, cells),
        )

    def _normalize_cell_map(self, cell_map: Any, sheet_name: str | None = None) -> dict[str, Cell]:
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
                    value=None
                    if formula is not None
                    else self._coerce_value(bare, None, address, sheet_name),
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
                value=self._coerce_value(
                    cell_data.get("value"), fmt, address, sheet_name, explicit=True
                ),
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

    def _normalize_widths(self, sheet: dict[str, Any]) -> dict[int, float]:
        """The two ways a width can be stated, folded into one map.

        ``columns[].width`` widens the column at that POSITION; the sheet-level
        ``columnWidths`` map keys the same columns by 0-based index. Both have
        been described in the shared ``skills/holy-sheet.schema.json`` ("Same as
        columnWidths but per-column") for as long as the field has existed, and
        only the map was ever read -- so a width written the way the schema
        documents it emitted no ``<cols>`` element at all, with no error and no
        repair note (holy-sheet#8; PHP 2.4.0 and Node 2.5.0 fixed it first, and
        PHP is normative here).

        The MAP IS APPLIED LAST and wins, matching PHP. It is the mechanism that
        already worked, so a consumer who reached for it to work around this bug
        must not now find a leftover ``width`` quietly overriding it.

        Sorted ascending on the way out: ``<col>`` children are expected in
        column order, and merging two sources means insertion order -- which IS
        observable here -- is no longer column order.
        """
        out: dict[int, float] = {}

        for col_idx, column_def in enumerate(sheet.get("columns") or []):
            # A string column (``columns: ["Account"]``) is a documented
            # shorthand and carries no width; ``.get`` on a str would raise.
            if not isinstance(column_def, dict) or "width" not in column_def:
                continue
            index = column_widths.index(col_idx)
            width = column_widths.width(column_def["width"])
            if index is None or width is None:
                continue
            out[index] = width

        for index, width in self._normalize_column_widths(sheet.get("columnWidths") or {}).items():
            out[index] = width

        return {index: out[index] for index in sorted(out)}

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

    def _build_cell(
        self,
        address: str,
        value: Any,
        column_format: CellFormat | None,
        sheet_name: str | None = None,
    ) -> Cell:
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
                value=self._coerce_value(
                    value.get("value"), merged, address, sheet_name, explicit=True
                ),
                formula=value.get("formula"),
                format=merged,
                comment=comment,
                cached_value=value.get("computedValue"),
            )

        value, formula = self._promote_formula(value)

        return Cell(
            address=address,
            value=None
            if formula is not None
            else self._coerce_value(value, column_format, address, sheet_name),
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

    def _coerce_value(
        self,
        value: Any,
        fmt: CellFormat | None,
        address: str = "?",
        sheet_name: str | None = None,
        explicit: bool = False,
    ) -> Any:
        """``explicit``: the value came from an object cell's ``value`` key.

        An explicit string is the caller saying "this text, exactly", so it is
        never coerced to a number: ``{"value": "+1"}`` stays "+1" and
        ``{"value": "007"}`` stays "007". Only a date/datetime display format
        still turns a string into a serial, because that conversion is what the
        format asks for. Bare values keep numeric-string coercion. Mirrors PHP
        ``Normalizer::coerceValue``.
        """
        if value is None:
            return None

        display = fmt.display_format if fmt else None
        if display in ("date", "datetime"):
            if isinstance(value, (datetime, date)) or (
                isinstance(value, str) and value.strip() != ""
            ):
                return DateConverter.to_serial(value, display == "datetime")

        if explicit and isinstance(value, str):
            return value

        # `bool` is checked implicitly by is_numeric_string, which rejects it --
        # otherwise `True` would be "numeric" and land in a numeric cell.
        if isinstance(value, str) and is_numeric_string(value):
            return numeric_string_to_number(value)

        if isinstance(value, (datetime, date)):
            return DateConverter.to_serial(value, True)

        # PHP's `instanceof Stringable` -- a type that says how it becomes a
        # string, rather than inheriting `object.__str__` (which falls back to
        # `__repr__`, a DEBUG representation and never a cell value).
        #
        # ONE EXCLUSION, and it is the trap in this guard: `bytes` DOES define
        # its own `__str__`, and it yields `b'x'`. Measured, not assumed. The
        # Node port has the identical trap with a different type --
        # `Array.prototype.toString` is also "its own", and joining is exactly
        # the corruption that guard exists to stop -- so each port names it in a
        # test. "Defines its own conversion" is true of a value that must not
        # pass, in two of the three runtimes.
        if (
            not isinstance(value, (bytes, bytearray, memoryview))
            and type(value).__str__ is not object.__str__
        ):
            return str(value)

        # REFUSE rather than cast. This used to read "not a scalar, so it must
        # be safe to stringify", and a LIST reaches it whenever a plain `rows`
        # form is nested one level too deep -- `rows: [[["x"]]]` instead of
        # `rows: [["x"]]` -- which needs no object-cell syntax and is exactly
        # the shape an AI-generated edit produces. In Python a TUPLE does it
        # too, a shape neither peer runtime can even express.
        #
        # This port's corruption was the VISIBLE kind -- `['x']` written into
        # the cell, which someone opening the sheet files a bug about, where the
        # Node port wrote `"x"` and looked correct. Both are corrupt and neither
        # named the cell, which was the actual production complaint in
        # holy-sheet#9. holy-sheet-py#1.
        #
        # The path is the cell's `Sheet!A1` location, not the
        # `sheets[0].rows[1]` schema path the Validator uses. Deliberate, and
        # the one place the two conventions differ: this fault is always ONE
        # CELL, and the A1 location is what an operator can act on.
        if not isinstance(value, (str, int, float, bool)):
            where = address if sheet_name is None else f"{sheet_name}!{address}"

            raise SchemaException.from_errors(
                [
                    {
                        "path": where,
                        "expected": (
                            "a string, number, bool, null, date, "
                            "or an object defining __str__"
                        ),
                        "got": type_of(value),
                        "value": value,
                        "hint": (
                            "This cell holds a list. The usual cause is a `rows` entry nested "
                            'one level too deep: `rows: [[["x"]]]` makes cell A1 a list, where '
                            '`rows: [["x"]]` makes it the string "x". For a cell with '
                            'formatting use the object form: {"value": "x", "format": {...}}.'
                        )
                        if is_list(value)
                        else (
                            "Give the cell a scalar or a date, or define __str__ on the type "
                            "so the conversion is yours rather than implicit."
                        ),
                    }
                ]
            )

        return value


_ROW_NUMBER = re.compile(r"^[1-9][0-9]{0,6}$")


def _php_numeric(value: Any) -> float | None:
    """PHP ``is_numeric`` then ``(float)``: bool is NOT numeric (check it first)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if is_numeric_string(value):
        return float(numeric_string_to_number(value))
    return None


def normalize_row_heights(heights: Any) -> dict[int, float]:
    """``rowHeights``: 1-based row number -> pixels, ascending. Mirrors PHP."""
    if not isinstance(heights, (dict, list)):
        return {}
    items = heights.items() if isinstance(heights, dict) else enumerate(heights)
    out: dict[int, float] = {}
    for row, px in items:
        n = _php_numeric(px)
        if _ROW_NUMBER.match(str(row)) and n is not None and n >= 0:
            out[int(str(row))] = n
    return dict(sorted(out.items()))


def normalize_print(print_spec: Any) -> dict[str, Any] | None:
    """``print``, normalised; None when it asks for nothing. Mirrors PHP."""
    if not isinstance(print_spec, dict):
        return None
    orientation = print_spec.get("orientation")
    orientation = orientation if orientation in ("portrait", "landscape") else None
    fit = print_spec.get("fitToWidth") is True
    page = print_spec.get("fitToPage") is True
    footer = None
    raw = print_spec.get("footer")
    if isinstance(raw, dict):
        footer = {k: raw[k] if isinstance(raw.get(k), str) else "" for k in ("left", "center", "right")}
        if footer["left"] == "" and footer["center"] == "" and footer["right"] == "":
            footer = None
    if orientation is None and not fit and not page and footer is None:
        return None
    return {"orientation": orientation, "fitToWidth": fit, "fitToPage": page, "footer": footer}


CHART_TYPES = ("column", "bar", "line", "pie")
"""The chart types the writer draws. Mirrors PHP Normalizer::CHART_TYPES."""

_HEX_COLOR = re.compile(r"^#([0-9A-Fa-f]{6})$")


def _key(value: Any, key: str) -> Any:
    """PHP ``$value[$key] ?? null`` on a value that may not be a map."""
    return value.get(key) if isinstance(value, dict) else None


def normalize_points(points: Any, count: int) -> list[dict[str, Any]]:
    # A series' coloured points, by index. Mirrors PHP Normalizer::normalizePoints:
    # an index outside the values or a colour that is not #RRGGBB is dropped, and a
    # repeated index keeps its last colour.
    if not is_array(points):
        return []
    by_index: dict[int, str] = {}
    for _, p in entries(points):
        index = _key(p, "index")
        color = _key(p, "color")
        match = _HEX_COLOR.match(color) if isinstance(color, str) else None
        # PHP is_int: a bool is not an int.
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < count and match:
            by_index[index] = match.group(1).upper()
    return [{"index": i, "color": by_index[i]} for i in sorted(by_index)]


def normalize_charts(charts: Any, sheet_name: str, cells: dict[str, Cell]) -> list[dict[str, Any]]:
    """``charts``, normalised. Mirrors PHP Normalizer::normalizeCharts rule for rule."""
    if not is_array(charts):
        return []
    last_col = -1
    for address in cells:
        parsed = CellAddress.parse(str(address))
        if parsed is not None:
            last_col = max(last_col, parsed[0])

    out: list[dict[str, Any]] = []
    for _, chart in entries(charts):
        # `in` on a tuple compares with ==, and True == 1; the type check keeps it strict.
        chart_type = _key(chart, "type")
        if not is_array(chart) or not isinstance(chart_type, str) or chart_type not in CHART_TYPES:
            continue
        series: list[dict[str, Any]] = []
        raw_series = _key(chart, "series")
        for _, s in entries(raw_series) if is_array(raw_series) else []:
            values = ChartRange.parse(_key(s, "values"), sheet_name) if is_array(s) else None
            if values is None:
                continue
            color = _key(s, "color")
            match = _HEX_COLOR.match(color) if isinstance(color, str) else None
            name = _key(s, "name")
            series.append({
                "name": name if isinstance(name, str) else None,
                "values": values,
                "color": match.group(1).upper() if match else None,
                "points": normalize_points(_key(s, "points"), len(values.addresses())),
            })
        if not series:
            continue

        stack = len(out)
        start = [last_col + 2, 1 + 18 * stack]
        end = [start[0] + 8, start[1] + 16]
        position = _key(chart, "position")
        if is_array(position):
            a = CellAddress.parse(_key(position, "from")) if isinstance(_key(position, "from"), str) else None
            b = CellAddress.parse(_key(position, "to")) if isinstance(_key(position, "to"), str) else None
            if a is not None and b is not None and b[0] > a[0] and b[1] > a[1]:
                start = [a[0], a[1] - 1]
                end = [b[0], b[1] - 1]

        title = _key(chart, "title")
        fmt = _key(chart, "format")
        legend = _key(chart, "legend")
        out.append({
            "type": chart_type,
            "title": title if isinstance(title, str) and title != "" else None,
            "categories": ChartRange.parse(_key(chart, "categories"), sheet_name),
            "series": series,
            "from": start,
            "to": end,
            "legend": legend if isinstance(legend, bool) else None,
            "dataLabels": _key(chart, "dataLabels") is True,
            "format": fmt if isinstance(fmt, str) and fmt != "" else None,
        })
    return out


def _comment_from(data: Any) -> CellComment | None:
    if not isinstance(data, dict):
        return None
    return CellComment(
        text=str(data.get("text", "")),
        author=data.get("author"),
        color=data.get("color"),
    )
