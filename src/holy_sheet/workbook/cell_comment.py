"""A cell note (Excel "comment")."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CellComment:
    text: str
    author: str | None = None
    # Hex; drives the corner triangle in some viewers. Excel ignores it.
    color: str | None = None
