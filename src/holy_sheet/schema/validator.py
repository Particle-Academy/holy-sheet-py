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

import re
from typing import Any

from ..exceptions import SchemaException
from ..helpers.php import entries, is_array, is_list, is_numeric_string, numeric_string_to_number, php_to_string, type_of
from ..workbook.cell_address import CellAddress
from ..workbook.chart_range import ChartRange
from . import column_widths
from .normalizer import CHART_TYPES
from .repairer import Repairer

_ROW_NUMBER = re.compile(r"^[1-9][0-9]{0,6}$")

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
                        continue

                    # A LIST as a cell value is the holy-sheet#9 shape: a row
                    # nested one level too deep. Caught HERE and not left to the
                    # writer, because `validate()` returning no errors for a
                    # schema `normalize()` throws on tells a caller the input is
                    # fine and moves the failure to where it is hardest to
                    # attribute. An agent that validates before writing -- the
                    # whole point of `validate()` -- was being told to go ahead.
                    #
                    # The test is `is_list` (PHP's `array_is_list`, so a tuple
                    # counts), NOT `is_array`: a DICT cell is the
                    # {"value": ..., "format": {...}} form and has always been
                    # legal. Rejecting any array would trade this bug for a far
                    # worse one -- every formatted cell in every workbook. There
                    # is a test holding that line.
                    for cell_key, cell in entries(row):
                        if is_list(cell):
                            errors.append(
                                _error(
                                    f"{path}.rows[{key}][{cell_key}]",
                                    "a cell value or a cell object",
                                    "array",
                                    cell,
                                    "This cell is a list, which usually means the row is "
                                    'nested one level too deep: `rows: [[["x"]]]` makes cell '
                                    'A1 a list, where `rows: [["x"]]` makes it "x". For a '
                                    'cell with formatting use the object form: '
                                    '{"value": "x", "format": {...}}.',
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

        # Mirrors PHP Validator, path and message for path and message.
        heights = _get(sheet, "rowHeights")
        if heights is not None:
            if not is_array(heights):
                errors.append(_error(f"{path}.rowHeights", "object keyed by 1-based row number", type_of(heights), heights,
                                     'Row heights map a row NUMBER to pixels: {"1": 32, "4": 28}.'))
            else:
                for row, px in entries(heights):
                    numeric = (
                        not isinstance(px, bool)
                        and (isinstance(px, (int, float)) or is_numeric_string(px))
                    )
                    if not _ROW_NUMBER.match(str(row)):
                        errors.append(_error(f"{path}.rowHeights.{row}", "a 1-based row number", type_of(row), row,
                                             'Keys are row numbers as Excel shows them: "1" is the first row.'))
                    elif not numeric or float(numeric_string_to_number(px) if isinstance(px, str) else px) < 0:
                        errors.append(_error(f"{path}.rowHeights.{row}", "a non-negative number of pixels", type_of(px), px,
                                             "A height is a number of pixels, like 28 (20 is a default row)."))

        if _has(sheet, "charts"):
            errors.extend(_validate_charts(sheet["charts"], path, php_to_string(_get(sheet, "name"))))

        print_spec = _get(sheet, "print")
        if print_spec is not None:
            if not is_array(print_spec):
                errors.append(_error(f"{path}.print", "object {orientation?, fitToWidth?, footer?}", type_of(print_spec), print_spec,
                                     'Print setup: {"orientation": "landscape", "fitToWidth": true, "footer": {"right": "Page {page} of {pages}"}}.'))
            else:
                orientation = _get(print_spec, "orientation")
                if orientation is not None and orientation not in ("portrait", "landscape"):
                    errors.append(_error(f"{path}.print.orientation", "one of: portrait, landscape", type_of(orientation), orientation,
                                         "Use landscape for a sheet wider than it is tall."))
                fit = _get(print_spec, "fitToWidth")
                if fit is not None and not isinstance(fit, bool):
                    errors.append(_error(f"{path}.print.fitToWidth", "boolean", type_of(fit), fit,
                                         "true scales the sheet to one page wide when printed."))
                page = _get(print_spec, "fitToPage")
                if page is not None and not isinstance(page, bool):
                    errors.append(_error(f"{path}.print.fitToPage", "boolean", type_of(page), page,
                                         "true scales the sheet onto ONE page, wide and tall: a one-page summary with a chart prints whole."))
                footer = _get(print_spec, "footer")
                if footer is not None:
                    if not is_array(footer):
                        errors.append(_error(f"{path}.print.footer", "object {left?, center?, right?}", type_of(footer), footer,
                                             "A footer has up to three slots of text."))
                    else:
                        for slot in ("left", "center", "right"):
                            value = _get(footer, slot)
                            if value is not None and not isinstance(value, str):
                                errors.append(_error(f"{path}.print.footer.{slot}", "string", type_of(value), value,
                                                     "Footer text; {page} and {pages} print the page number and count."))

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

        # Until holy-sheet#8 the writer ignored `width` entirely, so an invalid
        # one and a valid one were equally silent. Now that it is honoured, a
        # width that cannot be used has to say so -- the original defect's real
        # cost was that an agent composes against the schema doc, gets no error,
        # and never learns the field was discarded.
        if _has(column, "width") and column_widths.width(_get(column, "width")) is None:
            errors.append(
                _error(
                    f"{path}.width",
                    "a non-negative number of pixels",
                    type_of(_get(column, "width")),
                    _get(column, "width"),
                    "A column width is a number of pixels, like 120. The sheet-level "
                    "columnWidths map takes precedence over it.",
                )
            )

        return errors


def _error(path: str, expected: str, got: str, value: Any, hint: str) -> dict[str, Any]:
    return {"path": path, "expected": expected, "got": got, "value": value, "hint": hint}


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _validate_points(points: Any, at: str, values: ChartRange | None) -> list[dict[str, Any]]:
    # Mirrors PHP Validator::validatePoints.
    if not isinstance(points, (list, tuple)) and not (isinstance(points, dict) and len(points) == 0):
        return [_error(at, "a list of points", type_of(points), points,
                       'Colour single points: [{"index": 2, "color": "#C2410C"}], index 0-based within values.')]
    count = 0 if values is None else len(values.addresses())
    errors: list[dict[str, Any]] = []
    for k, p in entries(points):
        index = _get(p, "index") if is_array(p) else None
        color = _get(p, "color") if is_array(p) else None
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= count:
            errors.append(_error(f"{at}[{k}].index", "an index within values (0 to " + str(max(0, count - 1)) + ")", type_of(index), index,
                                 'The 0-based position of the point in its values range: "B5:B7" has points 0, 1 and 2.'))
        if not isinstance(color, str) or not _HEX_COLOR.match(color):
            errors.append(_error(f"{at}[{k}].color", "#RRGGBB", type_of(color), color, 'A hex colour, like "#C2410C".'))
    return errors


def _validate_charts(charts: Any, path: str, sheet_name: str) -> list[dict[str, Any]]:
    """Mirrors PHP Validator::validateCharts: path, expectation and hint for each."""
    # PHP: not an array, or a non-empty array that is not a list. `{}` decodes to [] there.
    if not is_array(charts) or (isinstance(charts, dict) and len(charts) > 0):
        return [_error(f"{path}.charts", "a list of charts", type_of(charts), charts,
                       'charts is a list: [{"type": "column", "categories": "A5:A7", "series": [{"name": "Revenue", "values": "B5:B7"}]}].')]
    errors: list[dict[str, Any]] = []
    for i, chart in entries(charts):
        at = f"{path}.charts[{i}]"
        if not is_array(chart):
            errors.append(_error(at, "object", type_of(chart), chart, "A chart is an object with a type and series."))
            continue
        chart_type = _get(chart, "type")
        if not isinstance(chart_type, str) or chart_type not in CHART_TYPES:
            errors.append(_error(f"{at}.type", "one of: " + ", ".join(CHART_TYPES), type_of(chart_type), chart_type,
                                 "column compares values across categories; bar does the same horizontally (long labels); line shows change over time; pie shows parts of one whole."))
        series = _get(chart, "series")
        if not isinstance(series, (list, tuple)) or len(series) == 0:
            errors.append(_error(f"{at}.series", "a non-empty list of series", type_of(series), series,
                                 'Each series names the range it plots: [{"name": "Revenue", "values": "B5:B7"}].'))
        else:
            for j, s in enumerate(series):
                values = _get(s, "values") if is_array(s) else s
                if not is_array(s) or ChartRange.parse(values, sheet_name) is None:
                    errors.append(_error(f"{at}.series[{j}].values", 'a cell range like "B5:B7"', type_of(values), values,
                                         "A range of cells: \"B5:B7\" on this sheet, or \"'Other sheet'!B5:B7\"."))
                elif _has(s, "color") and (not isinstance(s["color"], str) or not _HEX_COLOR.match(s["color"])):
                    errors.append(_error(f"{at}.series[{j}].color", "#RRGGBB", type_of(s["color"]), s["color"],
                                         'A hex colour, like "#1F3A5F".'))
                if is_array(s) and _has(s, "points"):
                    errors.extend(_validate_points(s["points"], f"{at}.series[{j}].points", ChartRange.parse(values, sheet_name)))
        if _has(chart, "categories") and ChartRange.parse(chart["categories"], sheet_name) is None:
            errors.append(_error(f"{at}.categories", 'a cell range like "A5:A7"', type_of(chart["categories"]), chart["categories"],
                                 'The labels along the axis, as a range: "A5:A7".'))
        if _has(chart, "position"):
            p = chart["position"]
            a = CellAddress.parse(p["from"]) if is_array(p) and isinstance(_get(p, "from"), str) else None
            b = CellAddress.parse(p["to"]) if is_array(p) and isinstance(_get(p, "to"), str) else None
            if a is None or b is None or b[0] <= a[0] or b[1] <= a[1]:
                errors.append(_error(f"{at}.position", '{"from": top-left cell, "to": bottom-right cell}', type_of(p), p,
                                     'Two cells, the second below and to the right of the first: {"from": "E2", "to": "L18"} is 7 columns by 16 rows.'))
        for key in ("title", "format"):
            if _has(chart, key) and not isinstance(chart[key], str):
                errors.append(_error(f"{at}.{key}", "string", type_of(chart[key]), chart[key],
                                     'An Excel number format for the axis and labels, like "$#,##0" or "0.0%".' if key == "format" else "The chart title."))
        for key in ("dataLabels", "legend"):
            if _has(chart, key) and not isinstance(chart[key], bool):
                errors.append(_error(f"{at}.{key}", "boolean", type_of(chart[key]), chart[key],
                                     "true prints each value on the chart." if key == "dataLabels" else "Defaults to on for several series or a pie."))
    return errors


def _get(container: Any, key: str) -> Any:
    return container.get(key) if isinstance(container, dict) else None


def _has(container: Any, key: str) -> bool:
    """PHP's `isset()`: a key present with a null value counts as absent."""
    return isinstance(container, dict) and container.get(key) is not None
