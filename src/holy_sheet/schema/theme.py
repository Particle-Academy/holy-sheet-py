"""Theme presets -- pre-baked `CellFormat` sets applied during normalization.

Each theme answers three questions: what does row 1 look like when the sheet has
columns, what does data row N look like (banding), and what does the totals row
look like. `None` means "no formatting", which is the whole of the `plain`
theme.
"""

from __future__ import annotations

from ..workbook.cell_format import CellFormat


class Theme:
    def __init__(self, key: str) -> None:
        self.key = key

    def header_format(self) -> CellFormat | None:
        if self.key in ("default", "business"):
            return CellFormat(
                bold=True,
                color="#FFFFFF",
                background_color="#1F2937" if self.key == "business" else "#374151",
            )
        if self.key == "minimal":
            return CellFormat(bold=True, border_bottom="#000000")
        return None

    def data_format(self, row_index_zero_based: int) -> CellFormat | None:
        """Banded rows on `default` and `business`; nothing anywhere else."""
        if self.key in ("default", "business") and row_index_zero_based % 2 == 1:
            return CellFormat(background_color="#F3F4F6")
        return None

    def totals_format(self) -> CellFormat | None:
        if self.key in ("default", "business", "minimal"):
            return CellFormat(bold=True, border_top="#000000")
        return None
