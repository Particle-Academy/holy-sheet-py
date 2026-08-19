"""Per-cell format.

Mirrors fancy-sheets' `CellFormat` shape so a `<Spreadsheet>` workbook passes
straight through without translation. The writer's `StylesRegistry` dedupes
equal formats -- a 10k-row sheet with one bold-header style records that style
once, not 10,000 times.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CellFormat:
    """Internal value object. Not part of the input contract.

    The INPUT is a plain dict (see `schema/types.py`); this is what the
    Normalizer produces from it, and like its PHP/TS peers it is a real object.
    """

    bold: bool = False
    italic: bool = False
    text_align: str | None = None  # 'left' | 'center' | 'right'
    display_format: str | None = None  # auto|text|number|date|datetime|percentage|currency
    decimals: int | None = None
    color: str | None = None  # hex
    background_color: str | None = None  # hex
    font_size: int | None = None
    border_top: str | None = None
    border_right: str | None = None
    border_bottom: str | None = None
    border_left: str | None = None
    currency: str | None = None  # ISO-4217

    def key(self) -> tuple:
        """Stable dedup key for StylesRegistry.

        PHP hashes a serialized array; a tuple is the same idea without the
        hashing, and cannot collide.
        """
        return (
            self.bold,
            self.italic,
            self.text_align,
            self.display_format,
            self.decimals,
            self.color,
            self.background_color,
            self.font_size,
            self.border_top,
            self.border_right,
            self.border_bottom,
            self.border_left,
            self.currency,
        )

    def is_empty(self) -> bool:
        """True when nothing is set -- the caller can skip emitting a style."""
        return (
            not self.bold
            and not self.italic
            and self.text_align is None
            and self.display_format is None
            and self.decimals is None
            and self.color is None
            and self.background_color is None
            and self.font_size is None
            and self.border_top is None
            and self.border_right is None
            and self.border_bottom is None
            and self.border_left is None
            and self.currency is None
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CellFormat":
        """Build from the loose input shape (camelCase keys, agent-emitted)."""

        def _int_or_none(key: str) -> int | None:
            value = data.get(key)
            return None if value is None else int(value)

        return CellFormat(
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            text_align=data.get("textAlign"),
            display_format=data.get("displayFormat"),
            decimals=_int_or_none("decimals"),
            color=data.get("color"),
            background_color=data.get("backgroundColor"),
            font_size=_int_or_none("fontSize"),
            border_top=data.get("borderTop"),
            border_right=data.get("borderRight"),
            border_bottom=data.get("borderBottom"),
            border_left=data.get("borderLeft"),
            currency=data.get("currency"),
        )

    def merge_with(self, other: "CellFormat | None") -> "CellFormat":
        """Merge `other` on top of this one -- `other` wins where it is set."""
        if other is None:
            return self
        return CellFormat(
            bold=other.bold or self.bold,
            italic=other.italic or self.italic,
            text_align=_coalesce(other.text_align, self.text_align),
            display_format=_coalesce(other.display_format, self.display_format),
            decimals=_coalesce(other.decimals, self.decimals),
            color=_coalesce(other.color, self.color),
            background_color=_coalesce(other.background_color, self.background_color),
            font_size=_coalesce(other.font_size, self.font_size),
            border_top=_coalesce(other.border_top, self.border_top),
            border_right=_coalesce(other.border_right, self.border_right),
            border_bottom=_coalesce(other.border_bottom, self.border_bottom),
            border_left=_coalesce(other.border_left, self.border_left),
            currency=_coalesce(other.currency, self.currency),
        )


def _coalesce(preferred, fallback):
    """PHP's `??` -- null falls through, everything else (0, "", False) wins."""
    return fallback if preferred is None else preferred
