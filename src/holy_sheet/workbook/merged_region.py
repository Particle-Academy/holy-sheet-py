"""A merged cell range."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MergedRegion:
    start: str
    end: str

    def ref(self) -> str:
        return f"{self.start}:{self.end}"
