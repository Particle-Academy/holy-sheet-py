"""The read path: an OOXML package -> a Holy Sheet schema.

Walks the container with `zipfile`, parses parts in dependency order (rels ->
styles -> shared strings -> comments -> worksheets), and assembles a `Workbook`
that mirrors what `Normalizer` would have produced from the equivalent input.
`describe()` then turns that back into a schema which is feed-it-to-write
compatible.

**Takes BYTES, not a path.** That is Node's shape rather than PHP's path-only
`describe`, and it is the better contract: it works for an upload, an HTTP
response body and a file alike, and the caller decides how the bytes were
obtained. `holy_sheet.describe(path)` is the thin path-taking wrapper for
parity with PHP.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from ..workbook.cell import Cell
from ..workbook.cell_comment import CellComment
from ..workbook.cell_format import CellFormat
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from .comments_parser import CommentsParser
from .format.date_inverter import DateInverter
from .rels_parser import RelsParser
from .shared_strings_parser import SharedStringsParser
from .styles_parser import StylesParser
from .worksheet_parser import WorksheetParser
from .xml import attr, find, find_all, parse_xml_or_none, text_of


class XlsxReader:
    def describe(self, data: bytes) -> dict[str, Any]:
        return self._workbook_to_schema(self.read_workbook(data))

    def read_workbook(self, data: bytes) -> Workbook:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as error:
            raise RuntimeError("[holy-sheet] cannot open the input as a zip archive") from error

        with archive:
            styles_index = StylesParser.parse(_read(archive, "xl/styles.xml"))
            shared_strings = SharedStringsParser.parse(_read(archive, "xl/sharedStrings.xml"))

            workbook_xml = _read(archive, "xl/workbook.xml")
            if workbook_xml is None:
                raise RuntimeError("[holy-sheet] missing xl/workbook.xml")

            workbook_rels = RelsParser.parse(_read(archive, "xl/_rels/workbook.xml.rels"))

            root = parse_xml_or_none(workbook_xml)
            if root is None:
                raise RuntimeError("[holy-sheet] failed to parse xl/workbook.xml")

            sheets: list[Sheet] = []
            i = 0
            for sheet_el in find_all(find(root, "sheets"), "sheet"):
                name = attr(sheet_el, "name") or ""
                rel_id = attr(sheet_el, "id") or ""
                target = workbook_rels.get(rel_id, {}).get("Target")
                if target is None:
                    continue

                sheet_path = "xl/" + target.lstrip("/")
                worksheet_xml = _read(archive, sheet_path)
                if worksheet_xml is None:
                    continue

                comments: dict[str, CellComment] = {}
                sheet_rels_xml = _read(archive, f"xl/worksheets/_rels/sheet{i + 1}.xml.rels")
                if sheet_rels_xml is not None:
                    sheet_rels = RelsParser.parse(sheet_rels_xml)
                    for rel in RelsParser.by_type(sheet_rels, "/comments").values():
                        comments_path = _resolve_relative_path(sheet_path, rel["Target"])
                        comments_xml = _read(archive, comments_path)
                        if comments_xml is not None:
                            comments.update(CommentsParser.parse(comments_xml))

                sheets.append(
                    WorksheetParser.parse(
                        worksheet_xml, name, styles_index, comments, shared_strings
                    )
                )
                i += 1

            meta = _parse_doc_props(_read(archive, "docProps/core.xml"))

        return Workbook(sheets=sheets, meta=meta)

    # ------------------------------------------------------------------ #

    def _workbook_to_schema(self, workbook: Workbook) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "sheets": [self._sheet_to_schema(sheet) for sheet in workbook.sheets]
        }
        if workbook.meta:
            schema["meta"] = workbook.meta
        return schema

    def _sheet_to_schema(self, sheet: Sheet) -> dict[str, Any]:
        cells: dict[str, Any] = {}
        for address, cell in sheet.cells.items():
            cell_schema = self._cell_to_schema(cell)
            if cell_schema is None:  # wholly-empty cell: absent means the same thing
                continue
            cells[address] = cell_schema

        out: dict[str, Any] = {"name": sheet.name, "cells": cells}
        if sheet.merged_regions:
            out["mergedRegions"] = [
                {"start": m.start, "end": m.end} for m in sheet.merged_regions
            ]
        if sheet.column_widths:
            out["columnWidths"] = dict(sheet.column_widths)
        if sheet.frozen_rows > 0:
            out["frozenRows"] = sheet.frozen_rows
        if sheet.frozen_cols > 0:
            out["frozenCols"] = sheet.frozen_cols
        return out

    def _cell_to_schema(self, cell: Cell) -> dict[str, Any] | None:
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


def _parse_doc_props(core_xml: bytes | None) -> dict[str, Any]:
    root = parse_xml_or_none(core_xml)
    if root is None:
        return {}
    meta: dict[str, Any] = {}
    creator = find(root, "creator")
    if creator is not None:
        meta["creator"] = text_of(creator)
    created = find(root, "created")
    if created is not None:
        meta["created"] = text_of(created)
    return meta


def _read(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return archive.read(name)
    except KeyError:
        return None


def _resolve_relative_path(base: str, target: str) -> str:
    """Resolve `../comments1.xml` against `xl/worksheets/sheet1.xml`."""
    base_dir = base.rsplit("/", 1)[0] if "/" in base else ""
    parts: list[str] = []
    for segment in f"{base_dir}/{target}".split("/"):
        if segment == "..":
            if parts:
                parts.pop()
        elif segment not in ("", "."):
            parts.append(segment)
    return "/".join(parts)
