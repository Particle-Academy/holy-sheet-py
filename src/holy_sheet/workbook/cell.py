"""A single cell: intent, not serialisation.

Value, optional formula, optional format, optional comment, optional cached
formula result. The writer's `StylesRegistry` resolves formats to xf indexes
during serialisation; nothing here knows about xml.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .cell_comment import CellComment
from .cell_format import CellFormat

CellValue = str | int | float | bool | None


@dataclass(frozen=True)
class Cell:
    address: str
    value: CellValue = None
    formula: str | None = None
    format: CellFormat | None = None
    comment: CellComment | None = None
    cached_value: CellValue = None

    def excel_type(self) -> str:
        """The OOXML `t` attribute this cell needs.

        `bool` is tested BEFORE the numeric fallthrough on purpose: in Python
        `isinstance(True, int)` is True, so a bool that reaches the numeric
        branch is written as `<v>1</v>` with no `t="b"` -- a spreadsheet that
        shows 1 where the author wrote TRUE.
        """
        if self.formula is not None:
            return "str"
        if isinstance(self.value, str):
            return "inlineStr"
        if isinstance(self.value, bool):  # before int, always
            return "b"
        return "n"

    def with_format(self, fmt: CellFormat | None) -> "Cell":
        return replace(self, format=fmt)

    def with_value(self, value: CellValue) -> "Cell":
        return replace(self, value=value)
