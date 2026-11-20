"""The read path: an OOXML package -> a Holy Sheet schema.

Walks the container through the bounded `SourceArchive` guard, parses parts in dependency order (rels ->
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

import re
from typing import Any

from ..archive.office_template import source_kind
from ..archive.source_archive import SourceArchive, open_for_reading
from ..workbook.cell_comment import CellComment
from ..workbook.sheet import Sheet
from ..workbook.workbook import Workbook
from . import chart_parser
from ..exceptions import MissingPartException, SheetNotFoundException
from .comments_parser import CommentsParser
from .read_limits import ReadLimits
from .rels_parser import RelsParser
from .shared_strings_parser import SharedStringsParser
from .styles_parser import StylesParser
from .workbook_schema import WorkbookSchema
from .worksheet_parser import WorksheetParser
from .xml import attr, find, find_all, parse_xml_or_none, text_of


class XlsxReader:
    def describe(self, data: bytes | SourceArchive, only: str | None = None, limits: ReadLimits | None = None) -> dict[str, Any]:
        return WorkbookSchema.from_workbook(self.read_workbook(data, only, limits))

    def read_workbook(self, data: bytes | SourceArchive, only: str | None = None, limits: ReadLimits | None = None) -> Workbook:
        """`only` reads just the sheet of that exact name (the others are never
        parsed); an unknown name raises SheetNotFoundException. Mirrors PHP
        `XlsxReader::readArchive`."""
        if limits is None:
            limits = ReadLimits.from_options()
        # An archive the sniffer already opened is read as is, not inflated twice.
        archive = data if isinstance(data, SourceArchive) else open_for_reading(data, None, limits.archive)
        kind = source_kind(archive)

        with archive:
            workbook_path = "xl/workbook.xml"
            workbook_xml = _read(archive, workbook_path)
            if workbook_xml is None:
                raise RuntimeError("[holy-sheet] missing xl/workbook.xml")

            workbook_rels = RelsParser.parse(_read(archive, RelsParser.rels_path_of(workbook_path)))

            # Styles and shared strings are wherever the workbook's
            # relationships say; the conventional names are only the fallback
            # for a package that declares neither.
            styles_index = StylesParser.parse(
                _workbook_part(archive, workbook_rels, workbook_path, "styles", "xl/styles.xml")
            )
            shared_strings = SharedStringsParser.parse(
                _workbook_part(archive, workbook_rels, workbook_path, "sharedStrings", "xl/sharedStrings.xml"),
                limits,
            )

            root = parse_xml_or_none(workbook_xml)
            if root is None:
                raise RuntimeError("[holy-sheet] failed to parse xl/workbook.xml")
            # The 1904 date system: `<workbookPr date1904="1"/>` (or "true").
            date1904 = attr(find(root, "workbookPr"), "date1904") in ("1", "true")

            if only is not None:
                names = [attr(s, "name") or "" for s in find_all(find(root, "sheets"), "sheet")]
                if only not in names:
                    raise SheetNotFoundException(only, names)

            sheets: list[Sheet] = []
            # The position in workbook.xml of each described sheet: activeTab
            # counts the <sheet> elements. Every listed sheet is described (a
            # missing part raises), but the mapping is kept explicit.
            positions: list[int] = []
            position = -1
            for sheet_el in find_all(find(root, "sheets"), "sheet"):
                position += 1
                name = attr(sheet_el, "name") or ""
                if only is not None and name != only:
                    continue
                rel_id = attr(sheet_el, "id") or ""
                sheet_rel = workbook_rels.get(rel_id)
                if sheet_rel is None or RelsParser.is_external(sheet_rel):
                    raise MissingPartException(None, workbook_path, rel_id)

                sheet_path = RelsParser.resolve(workbook_path, sheet_rel["Target"])
                worksheet_xml = _require(archive, sheet_path, workbook_path, rel_id)

                # The sheet's own rels part sits beside it, named after its
                # actual part -- not after its position in the tab strip, which
                # need not match (sheet3.xml can be the first tab).
                sheet_rels = RelsParser.parse(_read(archive, RelsParser.rels_path_of(sheet_path)))

                comments: dict[str, CellComment] = {}
                for c_id, rel in RelsParser.by_type(sheet_rels, "/comments").items():
                    comments_xml = _require(archive, RelsParser.resolve(sheet_path, rel["Target"]), sheet_path, c_id)
                    comments.update(CommentsParser.parse(comments_xml))

                sheet = WorksheetParser.parse(
                    worksheet_xml, name, styles_index, comments, shared_strings, limits
                )

                # Charts: sheet rels -> drawing -> drawing rels -> chart parts (see PHP).
                charts: list[dict[str, Any]] = []
                for d_id, rel in RelsParser.by_type(sheet_rels, "/drawing").items():
                    drawing_path = RelsParser.resolve(sheet_path, rel["Target"])
                    drawing_xml = _require(archive, drawing_path, sheet_path, d_id)
                    # A drawing with no rels part holds no chart; that is not a missing part.
                    drawing_rels_xml = _read(archive, RelsParser.rels_path_of(drawing_path))
                    if drawing_rels_xml is None:
                        continue
                    drawing_rels = RelsParser.parse(drawing_rels_xml)
                    for anchor in chart_parser.anchors(drawing_xml):
                        chart_rel = drawing_rels.get(anchor["rel"])
                        if chart_rel is None or RelsParser.is_external(chart_rel):
                            continue
                        chart_xml = _require(
                            archive, RelsParser.resolve(drawing_path, chart_rel["Target"]), drawing_path, anchor["rel"]
                        )
                        parsed = chart_parser.chart(chart_xml, name, anchor["from"], anchor["to"])
                        if parsed is not None:
                            charts.append(parsed)
                if charts:
                    sheet.charts = charts

                sheets.append(sheet)
                positions.append(position)

            meta = _parse_doc_props(_read(archive, "docProps/core.xml"))

        # A read narrowed to one sheet is not the workbook, so it names no active sheet.
        active = _active_sheet(root, positions, position) if only is None and sheets else None

        return Workbook(sheets=sheets, meta=meta, source_kind=kind, date1904=date1904, active_sheet=active)


_UNSIGNED = re.compile(r"[0-9]+", re.ASCII)


def _active_sheet(workbook: Any, positions: list[int], last_position: int) -> int:
    """The described sheet that opens: the first `<workbookView>`'s
    `activeTab`, clamped to the `<sheet>` elements workbook.xml lists. Absent,
    or anything but a non-negative integer, is the default, 0. A tab no
    described sheet sits at falls back to the first described sheet. Mirrors
    PHP `XlsxReader::activeSheet`. CC-fancy-0050."""
    tab = 0
    view = find(find(workbook, "bookViews"), "workbookView")
    if view is not None:
        # xsd:unsignedInt collapses whitespace; only the four XML spaces.
        raw = (attr(view, "activeTab") or "").strip(" \t\n\r")
        if _UNSIGNED.fullmatch(raw):
            # Unbounded here; PHP saturates. Either way the clamp takes it to the last sheet.
            tab = min(int(raw), max(0, last_position))
    return positions.index(tab) if tab in positions else 0


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


def _read(archive: SourceArchive, name: str) -> bytes | None:
    return archive.read(name)


def _require(archive: SourceArchive, part: str, source: str, rel_id: str) -> bytes:
    """A part a relationship points at. Missing, it raises: a part skipped
    silently is how a workbook read as having no sheets and no error."""
    data = archive.read(part)
    if data is None:
        raise MissingPartException(part, source, rel_id)
    return data


def _workbook_part(
    archive: SourceArchive,
    workbook_rels: dict[str, dict[str, str]],
    workbook_path: str,
    rel_type: str,
    fallback: str,
) -> bytes | None:
    """The workbook-level part of relationship type `rel_type` (its last URI
    segment, so the transitional and strict namespaces both match), or the
    conventional `fallback` when the workbook declares no such relationship. A
    declared part that is missing raises. Mirrors PHP `XlsxReader::workbookPart`."""
    for rel_id, rel in workbook_rels.items():
        if not RelsParser.is_external(rel) and rel["Type"].endswith("/" + rel_type):
            return _require(archive, RelsParser.resolve(workbook_path, rel["Target"]), workbook_path, rel_id)
    return archive.read(fallback)
