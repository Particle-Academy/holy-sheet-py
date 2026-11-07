"""How big a spreadsheet a read will expand, beyond the archive's byte limits.

A port of PHP `Reader\\ReadLimits`. The archive guard bounds the bytes of each
part; these bound what those bytes turn into, which is where a read spends its
memory: a 64 MiB part can hold millions of `<c>` elements, and one ODS cell can
repeat across a whole sheet.

- ``maxRows``, ``maxColumns``: per sheet, the highest row and column it uses (a
  cell, or a `<row>` / `<col>` element). Default: Excel's own sheet limits.
- ``maxCells``: the whole workbook, counted sheet by sheet.
- ``maxSharedStrings``: entries in xl/sharedStrings.xml.

Each check runs before the thing it bounds is built. Configured like the
archive limits: positive ints keyed by name; an unknown key or anything else
(a bool included -- `bool` is an `int` here) is ``ArchiveRefused("read_limits")``.
"""

from __future__ import annotations

import re
from typing import Any

from ..archive.source_archive import DEFAULT_LIMITS as DEFAULT_ARCHIVE_LIMITS
from ..archive.source_archive import resolve_limits
from ..exceptions import ArchiveRefused, ReadLimitExceeded
from ..workbook.cell_address import CellAddress
from .ods.ns import php_trim
from .xml import attr_local, stream_xml

#: Mirrors PHP `ReadLimits::DEFAULTS`; the reasons are in the 0.12.0 CHANGELOG entry.
DEFAULT_READ_LIMITS: dict[str, int] = {
    "maxRows": 1048576,
    "maxColumns": 16384,
    "maxCells": 20000000,
    "maxSharedStrings": 20000000,
}

_CANONICAL = re.compile(r"([A-Z]{1,7})([0-9]{1,15})", re.ASCII)
_DIGITS = re.compile(r"[0-9]+", re.ASCII)
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class _SheetCount:
    """One worksheet's running count while it streams."""

    def __init__(self) -> None:
        self.rows = 0
        self.columns = 0
        self.cells = 0
        self.seen_cols = False
        self.seen_sheet_data = False
        self.inside: str | None = None  # "cols" | "sheetData" | None: the depth-1 element we are in
        self.in_row = False


class ReadLimits:
    """One instance per read; it carries the running cell count."""

    def __init__(self, limits: dict[str, int], archive: dict[str, int] | None = None) -> None:
        self._limits = limits
        #: The archive limits, resolved over their defaults.
        self.archive: dict[str, int] = archive if archive is not None else dict(DEFAULT_ARCHIVE_LIMITS)
        self._cells = 0
        self._letters: dict[str, int] = {}

    @classmethod
    def from_options(cls, limits: Any = None) -> "ReadLimits":
        """Any of the nine limits, by name (since 0.13.0 the archive limits too).
        A read limit that is not a positive int, or a name that is no limit, is
        ``ArchiveRefused("read_limits")``; a bad archive limit is
        ``ArchiveRefused("archive_limits")``. The message names the setting.
        Every key is checked before anything is opened. Mirrors PHP
        `ReadLimits::from`."""
        limits = {} if limits is None else limits
        if not isinstance(limits, dict):
            raise ArchiveRefused("read_limits")
        read: dict[str, int] = {}
        archive: dict[str, Any] = {}
        for key, value in limits.items():
            if key in DEFAULT_ARCHIVE_LIMITS:
                archive[key] = value
                continue
            known = key in DEFAULT_READ_LIMITS
            if not known or isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ArchiveRefused.bad_setting("read_limits", key, known)
            read[key] = value
        return cls({**DEFAULT_READ_LIMITS, **read}, resolve_limits(archive))

    def max(self, limit: str) -> int:
        return self._limits[limit] if limit in self._limits else self.archive[limit]

    def shared_strings(self, count: int) -> None:
        _check("maxSharedStrings", self._limits["maxSharedStrings"], count)

    def sheet(self, rows: int, columns: int, cells: int) -> None:
        """One sheet's extent and cell count, checked in order: rows, columns, cells."""
        _check("maxRows", self._limits["maxRows"], rows)
        _check("maxColumns", self._limits["maxColumns"], columns)
        self._cells += cells
        _check("maxCells", self._limits["maxCells"], self._cells)

    def worksheet_part(self, xml: bytes | str | None) -> None:
        """Count a worksheet part BEFORE it is parsed: the highest row a
        `<row r>` or a cell uses, the highest column a cell or a `<col max>`
        uses, and every `<c>`. Streams the part (`stream_xml`), building no
        tree: until 0.13.0 this walked the parsed tree, so a limit refused a
        10,000,000-cell sheet only after the whole tree existed. Matches what the
        parse reads: the root's first `cols` and `sheetData`, their `col` / `row`
        children and a row's `c` children, by local name. The cell count refuses
        the moment the workbook passes ``maxCells`` (the message says "at
        least"); a malformed part is counted up to the error. Mirrors PHP
        `ReadLimits::worksheetPart`."""
        count = _SheetCount()
        max_cells = self._limits["maxCells"]

        def visit(depth: int, name: str, attrib: dict[str, str]) -> None:
            if depth == 1:
                count.inside = None
                if name == "cols" and not count.seen_cols:
                    count.inside, count.seen_cols = "cols", True
                elif name == "sheetData" and not count.seen_sheet_data:
                    count.inside, count.seen_sheet_data = "sheetData", True
                return
            if depth == 2:
                count.in_row = False
                if count.inside == "cols" and name == "col":
                    count.columns = max(count.columns, _number(attr_local(attrib, "max")))
                elif count.inside == "sheetData" and name == "row":
                    count.rows = max(count.rows, _number(attr_local(attrib, "r")))
                    count.in_row = True
                return
            if depth != 3 or not count.in_row or name != "c":
                return
            count.cells += 1
            if count.cells + self._cells > max_cells:
                raise ReadLimitExceeded("maxCells", max_cells, count.cells + self._cells)
            count.columns, count.rows = self._cell_extent(attr_local(attrib, "r") or "", count.columns, count.rows)

        stream_xml(xml, 3, visit)
        self.sheet(count.rows, count.columns, count.cells)

    def shared_strings_part(self, xml: bytes | str | None) -> None:
        """Count a shared-strings part's `si` entries BEFORE it is parsed.
        Mirrors PHP `ReadLimits::sharedStringsPart`."""
        count = 0

        def visit(depth: int, name: str, attrib: dict[str, str]) -> None:
            nonlocal count
            if depth == 1 and name == "si":
                count += 1

        stream_xml(xml, 1, visit)
        self.shared_strings(count)

    def _cell_extent(self, ref: str, columns: int, rows: int) -> tuple[int, int]:
        """The extent one cell address adds: (columns, rows) after it."""
        m = _CANONICAL.fullmatch(ref)
        if m is not None:
            # The canonical form every writer emits; address() gives the same answer.
            letters = m.group(1)
            col_index = self._letters.get(letters)
            if col_index is None:
                col_index = CellAddress.index(letters)
                if len(self._letters) < 20000:
                    self._letters[letters] = col_index
            return max(columns, col_index + 1), max(rows, int(m.group(2)))
        at = address(ref)
        return (columns, rows) if at is None else (max(columns, at[0] + 1), max(rows, at[1]))


def address(value: str) -> tuple[int, int] | None:
    """(column index, row) of an A1 address, or None. Mirrors PHP
    `ReadLimits::address`: trim()'s set, ASCII upper-casing only, at most seven
    letters and fifteen digits."""
    m = _CANONICAL.fullmatch(php_trim(value).translate(_ASCII_UPPER))
    return (CellAddress.index(m.group(1)), int(m.group(2))) if m is not None else None


def _number(value: str | None) -> int:
    """A row or column number from an attribute; mirrors PHP `ReadLimits::number`."""
    text = php_trim(value or "")
    if _DIGITS.fullmatch(text) is None:
        return 0
    return 10**15 if len(text.lstrip("0")) > 15 else int(text)


def _check(limit: str, maximum: int, actual: int) -> None:
    if actual > maximum:
        raise ReadLimitExceeded(limit, maximum, actual)
