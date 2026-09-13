"""OpenDocument Spreadsheet (.ods) reader: bytes -> Holy Sheet schema, the same
schema the xlsx reader returns.

Mirrors PHP `Reader\\OdsReader`, where what is and is not mapped is documented.
Takes BYTES, like `XlsxReader`.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree.ElementTree import Element

from ..exceptions import UnsupportedFormatException
from ..helpers.php import php_round
from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
from ..workbook.cell_comment import CellComment
from ..workbook.merged_region import MergedRegion
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from .ods import ns
from .ods.ods_formula import ods_formula_to_a1
from .ods.ods_styles import OdsStyles
from .ods.ods_text import paragraphs
from .workbook_schema import WorkbookSchema
from .xml import parse_xml_or_none

#: Sheet bounds. A repeat past them is padding, and is not followed.
MAX_ROWS = 1048576
MAX_COLUMNS = 16384

#: Days from the spreadsheet epoch (1899-12-30) to the Unix epoch.
UNIX_EPOCH_SERIAL = 25569

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_INTEGER = re.compile(r"-?[0-9]+", re.ASCII)
_DATE = re.compile(
    r"(-?[0-9]{4,})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2})(?::([0-9]{2})(\.[0-9]+)?)?)?(Z|[+-][0-9]{2}:?[0-9]{2})?",
    re.ASCII,
)
_DURATION = re.compile(r"(-)?P(?:([0-9]+)D)?(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+(?:\.[0-9]+)?)S)?)?", re.ASCII)
_LITERAL_BOOLEAN = re.compile(r"[ \t\n\r\f\v]*(TRUE|FALSE)\(\)[ \t\n\r\f\v]*", re.IGNORECASE | re.ASCII)
_ZONED = re.compile(r".*(Z|[+-][0-9]{2}:?[0-9]{2})", re.ASCII | re.DOTALL)

_ROW_CONTAINERS = frozenset({"table-header-rows", "table-rows", "table-row-group"})
_COLUMN_CONTAINERS = frozenset({"table-columns", "table-header-columns", "table-column-group"})


@dataclass(frozen=True)
class _ReadCell:
    """Everything about a cell that does not depend on where it is."""

    value: Any
    formula: str | None
    cached: Any
    type: str | None
    currency: str | None
    date_has_time: bool
    comment: CellComment | None
    #: A date or time value as Unix seconds (UTC), until the format decides date or datetime.
    seconds: int | None


class OdsReader:
    def describe(self, data: bytes) -> dict[str, Any]:
        return WorkbookSchema.from_workbook(self.read_workbook(data))

    def read_workbook(self, data: bytes) -> Workbook:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise UnsupportedFormatException.not_a_zip() from error

        with archive:
            content = _read(archive, "content.xml")
            styles = _read(archive, "styles.xml")
            meta = _read(archive, "meta.xml")

        if content is None:
            raise RuntimeError("[holy-sheet] missing content.xml")
        content_root = parse_xml_or_none(content)
        if content_root is None:
            raise RuntimeError("[holy-sheet] failed to parse content.xml")

        style_index = OdsStyles(content_root, parse_xml_or_none(styles))

        sheets: list[Sheet] = []
        spreadsheet = ns.child(ns.child(content_root, ns.OFFICE, "body"), ns.OFFICE, "spreadsheet")
        for table in ns.children_in(spreadsheet, ns.TABLE):
            if ns.local(table) == "table":
                sheets.append(self._read_sheet(table, style_index))

        return Workbook(sheets=sheets, meta=_read_meta(meta) if meta is not None else {})

    def _read_sheet(self, table: Element, styles: OdsStyles) -> Sheet:
        columns: list[tuple[int, int, str]] = []
        _collect_columns(table, columns, [0])

        state = {"row": 0, "cells": {}, "merges": []}
        self._walk_rows(table, styles, columns, state)

        return Sheet(
            name=ns.attr(table, ns.TABLE, "name") or "",
            cells=state["cells"],
            merged_regions=state["merges"],
        )

    def _walk_rows(self, container: Element, styles: OdsStyles, columns: list[tuple[int, int, str]], state: dict[str, Any]) -> None:
        for el in ns.children_in(container, ns.TABLE):
            if state["row"] >= MAX_ROWS:
                return
            name = ns.local(el)
            if name == "table-row":
                self._read_row(el, styles, columns, state)
            elif name in _ROW_CONTAINERS:
                self._walk_rows(el, styles, columns, state)

    def _read_row(self, row: Element, styles: OdsStyles, columns: list[tuple[int, int, str]], state: dict[str, Any]) -> None:
        repeat = max(1, ns.php_int(ns.attr(row, ns.TABLE, "number-rows-repeated") or "1"))
        row_style = ns.attr(row, ns.TABLE, "default-cell-style-name") or ""

        # One pass over the cells, reading each one that holds anything ONCE,
        # however many columns and rows it repeats across.
        entries: list[tuple[int, _ReadCell | None, tuple[int, int], str]] = []
        column = 0
        for cell in ns.children_in(row, ns.TABLE):
            name = ns.local(cell)
            if name not in ("table-cell", "covered-table-cell"):
                continue

            if name == "table-cell":
                span = (
                    max(1, ns.php_int(_attr_or(cell, ns.TABLE, "number-columns-spanned", "1"))),
                    max(1, ns.php_int(_attr_or(cell, ns.TABLE, "number-rows-spanned", "1"))),
                )
            else:
                span = (1, 1)
            read = _read_cell(cell) if _has_content(cell) else None

            count = max(1, ns.php_int(_attr_or(cell, ns.TABLE, "number-columns-repeated", "1")))
            if read is not None or span != (1, 1):
                style = ns.attr(cell, ns.TABLE, "style-name") or ""
                i = 0
                while i < count and column + i < MAX_COLUMNS:
                    entries.append((column + i, read, span, style))
                    i += 1

            column += count
            if column >= MAX_COLUMNS:
                break

        if not entries:
            state["row"] += repeat
            return

        r = 0
        while r < repeat and state["row"] < MAX_ROWS:
            row_number = state["row"] + 1
            for col, read, span, style in entries:
                address = CellAddress.letter(col) + str(row_number)

                if span != (1, 1):
                    state["merges"].append(
                        MergedRegion(
                            address,
                            CellAddress.letter(min(col + span[0], MAX_COLUMNS) - 1)
                            + str(min(row_number + span[1] - 1, MAX_ROWS)),
                        )
                    )
                if read is None:
                    continue

                style_name = style if style != "" else (row_style if row_style != "" else _column_style(columns, col))
                fmt = styles.cell_format(style_name, read.type, read.currency, read.date_has_time)
                value = read.value
                if read.seconds is not None:
                    # A date or time value always has a date or datetime format by now.
                    value = _iso_from_seconds(read.seconds, fmt is None or fmt.display_format != "date")
                state["cells"][address] = Cell(
                    address=address,
                    value=value,
                    formula=read.formula,
                    format=fmt,
                    comment=read.comment,
                    cached_value=read.cached,
                )
            state["row"] += 1
            r += 1


def _attr_or(element: Element, namespace: str, name: str, default: str) -> str:
    value = ns.attr(element, namespace, name)
    return default if value is None else value


def _collect_columns(container: Element, columns: list[tuple[int, int, str]], cursor: list[int]) -> None:
    """Column default cell styles as (first index, last index, style name)."""
    for el in ns.children_in(container, ns.TABLE):
        name = ns.local(el)
        if name == "table-column":
            repeat = max(1, ns.php_int(_attr_or(el, ns.TABLE, "number-columns-repeated", "1")))
            style = ns.attr(el, ns.TABLE, "default-cell-style-name") or ""
            if style != "":
                columns.append((cursor[0], cursor[0] + repeat - 1, style))
            cursor[0] += repeat
        elif name in _COLUMN_CONTAINERS:
            _collect_columns(el, columns, cursor)
        if cursor[0] >= MAX_COLUMNS:
            return


def _column_style(columns: list[tuple[int, int, str]], column: int) -> str:
    for first, last, style in columns:
        if first <= column <= last:
            return style
    return "Default"


def _has_content(cell: Element) -> bool:
    """A value type, a formula, a comment, or text. A cell with only a style is formatting over nothing."""
    if ns.attr(cell, ns.OFFICE, "value-type") is not None:
        return True
    if ns.attr(cell, ns.TABLE, "formula") is not None:
        return True
    if ns.child(cell, ns.OFFICE, "annotation") is not None:
        return True
    return (paragraphs(cell) or "") != ""


def _read_cell(cell: Element) -> _ReadCell:
    value_type = ns.attr(cell, ns.OFFICE, "value-type")
    text = paragraphs(cell)

    value: Any = None
    seconds: int | None = None
    date_has_time = False
    if value_type in ("float", "percentage", "currency"):
        value = _to_number(_attr_or(cell, ns.OFFICE, "value", ""))
    elif value_type == "boolean":
        value = ns.ascii_lower(ns.php_trim(_attr_or(cell, ns.OFFICE, "boolean-value", ""))) in ("true", "1")
    elif value_type == "date":
        raw = ns.php_trim(_attr_or(cell, ns.OFFICE, "date-value", ""))
        seconds = _date_seconds(raw)
        date_has_time = "T" in raw
    elif value_type == "time":
        duration = _duration_seconds(_attr_or(cell, ns.OFFICE, "time-value", ""))
        seconds = None if duration is None else duration - UNIX_EPOCH_SERIAL * 86400
    elif value_type == "string":
        error = ns.attr(cell, ns.CALCEXT, "value-type") == "error"
        stored = ns.attr(cell, ns.OFFICE, "string-value")
        value = stored if stored is not None and not error else (text if text is not None else "")
    else:
        value = text if text is not None and text != "" else None

    formula: str | None = None
    cached: Any = None
    formula_attr = ns.attr(cell, ns.TABLE, "formula")
    if formula_attr is not None:
        translated = ods_formula_to_a1(formula_attr)
        literal_boolean = value_type == "boolean" and _LITERAL_BOOLEAN.fullmatch(translated) is not None
        if not literal_boolean:
            formula = translated
            cached = _serial(seconds) if seconds is not None else value
            value = None
            seconds = None

    comment: CellComment | None = None
    annotation = ns.child(cell, ns.OFFICE, "annotation")
    if annotation is not None:
        creator = ns.child(annotation, ns.DC, "creator")
        author = ns.php_trim(ns.own_text(creator)) if creator is not None else ""
        comment = CellComment(paragraphs(annotation) or "", author if author != "" else None)

    currency = ns.attr(cell, ns.OFFICE, "currency")
    return _ReadCell(
        value=value,
        formula=formula,
        cached=cached,
        type=value_type,
        currency=ns.php_trim(currency) if currency is not None else None,
        date_has_time=date_has_time,
        comment=comment,
        seconds=seconds,
    )


def _to_number(raw: str) -> int | float | None:
    """`office:value` as the xlsx reader coerces `<v>`: an integer when it is one."""
    text = ns.php_trim(raw)
    if text == "":
        return None
    if _INTEGER.fullmatch(text):
        return int(text)
    return float(text) if ns.php_is_numeric(text) else None


def _date_seconds(raw: str) -> int | None:
    """`office:date-value` -> whole seconds since the Unix epoch, in UTC."""
    m = _DATE.fullmatch(raw)
    if m is None:
        return None

    days = _days_from_civil(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    seconds = (
        days * 86400
        + ns.php_int(m.group(4) or "0") * 3600
        + ns.php_int(m.group(5) or "0") * 60
        + ns.php_int(m.group(6) or "0")
    )
    fraction = float("0" + m.group(7)) if m.group(7) else 0.0

    zone = m.group(8) or ""
    if zone not in ("", "Z"):
        digits = zone[1:].replace(":", "")
        offset = ns.php_int(digits[0:2]) * 3600 + ns.php_int(digits[2:4]) * 60
        seconds -= -offset if zone[0] == "-" else offset

    return seconds + int(php_round(fraction))


def _duration_seconds(raw: str) -> int | None:
    """`office:time-value` (`PT10H30M00S`, `P1DT2H`, `-PT1H`) -> whole seconds."""
    m = _DURATION.fullmatch(ns.php_trim(raw))
    if m is None:
        return None
    total = (
        ns.php_int(m.group(2) or "0") * 86400
        + ns.php_int(m.group(3) or "0") * 3600
        + ns.php_int(m.group(4) or "0") * 60
        + (float(m.group(5)) if m.group(5) else 0)
    )
    seconds = int(php_round(float(total)))
    return -seconds if m.group(1) == "-" else seconds


def _intdiv(a: int, b: int) -> int:
    """PHP `intdiv`: truncates toward zero, where `//` floors."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 in the proleptic Gregorian calendar (Hinnant's algorithm)."""
    year -= 1 if month <= 2 else 0
    era = _intdiv(year if year >= 0 else year - 399, 400)
    yoe = year - era * 400
    doy = _intdiv(153 * (month + (-3 if month > 2 else 9)) + 2, 5) + day - 1
    doe = yoe * 365 + _intdiv(yoe, 4) - _intdiv(yoe, 100) + doy
    return era * 146097 + doe - 719468


def _serial(seconds: int) -> int | float:
    """Unix seconds -> spreadsheet serial, an integer when it is a whole day."""
    total = seconds + UNIX_EPOCH_SERIAL * 86400
    return total // 86400 if total % 86400 == 0 else total / 86400


def _iso_from_seconds(seconds: int, with_time: bool) -> str:
    """PHP `gmdate('Y-m-d')` / `gmdate('Y-m-d\\TH:i:s\\Z')`."""
    try:
        moment = _UNIX_EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        # Outside years 1-9999, which `datetime` cannot hold. No spreadsheet
        # stores one; say nothing rather than raise on a hostile file.
        return ""
    date = f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
    if not with_time:
        return date
    return f"{date}T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}Z"


def _read_meta(xml: bytes) -> dict[str, Any]:
    root = parse_xml_or_none(xml)
    meta = ns.child(root, ns.OFFICE, "meta")
    if meta is None:
        return {}

    out: dict[str, Any] = {}
    creator = ns.child(meta, ns.META, "initial-creator")
    if creator is None:
        creator = ns.child(meta, ns.DC, "creator")
    if creator is not None and ns.php_trim(ns.own_text(creator)) != "":
        out["creator"] = ns.php_trim(ns.own_text(creator))
    created = ns.child(meta, ns.META, "creation-date")
    if created is not None and ns.php_trim(ns.own_text(created)) != "":
        value = ns.php_trim(ns.own_text(created))
        # ODF dates carry no zone unless one is written; the schema's are UTC.
        out["created"] = value if _ZONED.fullmatch(value) else value + "Z"
    return out


def _read(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return archive.read(name)
    except KeyError:
        return None
