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
from xml.etree.ElementTree import Element

from ..exceptions import ArchiveRefused, ReadLimitExceeded
from ..workbook.cell_address import CellAddress
from .ods.ns import php_trim
from .xml import attr, find, find_all

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


class ReadLimits:
    """One instance per read; it carries the running cell count."""

    def __init__(self, limits: dict[str, int]) -> None:
        self._limits = limits
        self._cells = 0
        self._letters: dict[str, int] = {}

    @classmethod
    def from_options(cls, limits: Any = None) -> "ReadLimits":
        limits = {} if limits is None else limits
        if not isinstance(limits, dict):
            raise ArchiveRefused("read_limits")
        for key, value in limits.items():
            if key not in DEFAULT_READ_LIMITS or isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ArchiveRefused("read_limits")
        return cls({**DEFAULT_READ_LIMITS, **limits})

    def max(self, limit: str) -> int:
        return self._limits[limit]

    def shared_strings(self, count: int) -> None:
        _check("maxSharedStrings", self._limits["maxSharedStrings"], count)

    def sheet(self, rows: int, columns: int, cells: int) -> None:
        """One sheet's extent and cell count, checked in order: rows, columns, cells."""
        _check("maxRows", self._limits["maxRows"], rows)
        _check("maxColumns", self._limits["maxColumns"], columns)
        self._cells += cells
        _check("maxCells", self._limits["maxCells"], self._cells)

    def worksheet(self, root: Element) -> None:
        """Count a parsed worksheet: the highest row a `<row r>` or a cell uses,
        the highest column a cell or a `<col max>` uses, and every `<c>`."""
        rows = columns = cells = 0
        for col in find_all(find(root, "cols"), "col"):
            columns = max(columns, _number(attr(col, "max")))
        for row in find_all(find(root, "sheetData"), "row"):
            rows = max(rows, _number(attr(row, "r")))
            for c in find_all(row, "c"):
                cells += 1
                ref = attr(c, "r") or ""
                m = _CANONICAL.fullmatch(ref)
                if m is not None:
                    # The canonical form every writer emits; address() gives the same answer.
                    letters = m.group(1)
                    col_index = self._letters.get(letters)
                    if col_index is None:
                        col_index = CellAddress.index(letters)
                        if len(self._letters) < 20000:
                            self._letters[letters] = col_index
                    columns = max(columns, col_index + 1)
                    rows = max(rows, int(m.group(2)))
                    continue
                at = address(ref)
                if at is not None:
                    columns = max(columns, at[0] + 1)
                    rows = max(rows, at[1])
        self.sheet(rows, columns, cells)


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
