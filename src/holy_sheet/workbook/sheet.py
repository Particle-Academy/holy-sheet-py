"""A worksheet: an A1-keyed sparse cell map plus its sheet-level furniture."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cell import Cell
from .cell_comment import CellComment
from .merged_region import MergedRegion

_A1 = re.compile(r"^([A-Z]+)(\d+)$")


@dataclass
class Sheet:
    name: str
    # A1 -> Cell, in DOCUMENT ORDER. Python dicts are insertion-ordered, so the
    # ordering contract the Node port needed a deliberate structure for is free
    # here -- but it is a contract, not an accident: `comments()` and the
    # comment/vml part numbering both read this order.
    cells: dict[str, Cell] = field(default_factory=dict)
    merged_regions: list[MergedRegion] = field(default_factory=list)
    # 0-based column index -> width in pixels.
    column_widths: dict[int, float] = field(default_factory=dict)
    frozen_rows: int = 0
    frozen_cols: int = 0

    def rows(self) -> dict[int, dict[str, Cell]]:
        """Group cells by 1-based row number: {row: {column letter: Cell}}.

        Rows come out in ascending numeric order. The COLUMN order inside each
        row is decided by the writer, and it sorts the letters
        lexicographically -- see `XlsxWriter.sheet_xml`, which explains why.
        Malformed addresses are skipped, matching the reference engine.
        """
        rows: dict[int, dict[str, Cell]] = {}
        for address, cell in self.cells.items():
            match = _A1.match(address)
            if match is None:
                continue
            rows.setdefault(int(match.group(2)), {})[match.group(1)] = cell
        return {row: rows[row] for row in sorted(rows)}

    def has_comments(self) -> bool:
        return any(cell.comment is not None for cell in self.cells.values())

    def comments(self) -> list[tuple[str, CellComment]]:
        """(address, comment) in document order."""
        return [
            (cell.address, cell.comment)
            for cell in self.cells.values()
            if cell.comment is not None
        ]
