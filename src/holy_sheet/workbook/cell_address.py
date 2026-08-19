"""A1 cell-address utilities -- column letter <-> 0-based index, parsing.

Used by the writer (assembling sheet xml), the reader (parsing sheetN.xml), the
normalizer (column placement in row-oriented mode) and the formula linter
(expanding ranges).
"""

from __future__ import annotations

import re

_A1 = re.compile(r"^([A-Z]+)(\d+)$")
_LETTERS = re.compile(r"^[A-Z]+$")


class CellAddress:
    """Namespace class, mirroring the PHP/TS peers' static-method shape."""

    @staticmethod
    def letter(index: int) -> str:
        """0-based column index -> Excel column letters (0 -> A, 26 -> AA)."""
        if index < 0:
            raise ValueError(f"[holy-sheet] column index must be >= 0, got {index}")
        letters = ""
        n = index
        while True:
            letters = chr(65 + (n % 26)) + letters
            n = n // 26 - 1
            if n < 0:
                break
        return letters

    @staticmethod
    def index(letters: str) -> int:
        """Column letters -> 0-based index."""
        letters = letters.strip().upper()
        if not _LETTERS.match(letters):
            raise ValueError(f"[holy-sheet] invalid column letters: '{letters}'")
        idx = 0
        for char in letters:
            idx = idx * 26 + (ord(char) - 64)
        return idx - 1

    @staticmethod
    def parse(address: str) -> tuple[int, int] | None:
        """A1 -> (column index, 1-based row), or None when malformed."""
        match = _A1.match(address.strip().upper())
        if match is None:
            return None
        return CellAddress.index(match.group(1)), int(match.group(2))
