"""A cell range a chart reads from. Mirrors PHP ``Workbook\\ChartRange``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cell_address import CellAddress


@dataclass(frozen=True)
class ChartRange:
    """"B5:B7", "B5", "'Q3 Revenue'!B5:B7" or "Data!B5:B7".

    Resolved against the chart's own sheet when no sheet is named.
    ``c1``/``c2`` are 0-based columns, ``r1``/``r2`` 1-based rows.
    """

    sheet: str
    c1: int
    r1: int
    c2: int
    r2: int

    @staticmethod
    def parse(ref: Any, default_sheet: str) -> ChartRange | None:
        if not isinstance(ref, str):
            return None
        text = ref.strip()
        sheet = default_sheet
        bang = text.rfind("!")
        if bang >= 0:
            sheet_part = text[:bang]
            text = text[bang + 1:]
            if len(sheet_part) >= 2 and sheet_part.startswith("'") and sheet_part.endswith("'"):
                sheet_part = sheet_part[1:-1].replace("''", "'")
            if sheet_part == "":
                return None
            sheet = sheet_part
        ends = text.replace("$", "").split(":")
        if len(ends) > 2:
            return None
        a = CellAddress.parse(ends[0])
        b = CellAddress.parse(ends[1] if len(ends) > 1 else ends[0])
        if a is None or b is None or a[1] < 1 or b[1] < 1:
            return None
        return ChartRange(sheet, min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

    def formula(self) -> str:
        """'Q3 Revenue'!$B$5:$B$7"""
        start = "$" + CellAddress.letter(self.c1) + "$" + str(self.r1)
        end = "$" + CellAddress.letter(self.c2) + "$" + str(self.r2)
        return "'" + self.sheet.replace("'", "''") + "'!" + start + ("" if start == end else ":" + end)

    def to_schema(self, own_sheet: str) -> str:
        """"B5:B7" on its own sheet, "'Data'!B5:B7" on another."""
        start = CellAddress.letter(self.c1) + str(self.r1)
        end = CellAddress.letter(self.c2) + str(self.r2)
        ref = start if start == end else start + ":" + end
        return ref if self.sheet == own_sheet else "'" + self.sheet.replace("'", "''") + "'!" + ref

    def addresses(self) -> list[str]:
        """A1 addresses, row by row."""
        return [
            CellAddress.letter(c) + str(r)
            for r in range(self.r1, self.r2 + 1)
            for c in range(self.c1, self.c2 + 1)
        ]
