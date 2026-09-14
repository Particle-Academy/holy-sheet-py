"""What a ``columnWidths`` entry may be.

One rule for the validator, the repairer and the normalizer, and the same rule
as PHP ``HolySheet\\Schema\\ColumnWidths`` (holy-sheet 2.3.4) and the Node port:

- a KEY is a 0-based column index, 0 to ``MAX_INDEX`` (Excel's last column,
  XFD): an ``int``, or a string of one to five ASCII digits;
- a WIDTH is a non-negative finite number (never a ``bool``), or a string of
  ASCII digits with an optional decimal part (``"120"``, ``"80.5"``).

Before 0.3.2 the normalizer did ``int(key)`` and ``float(px)``, so a key like
``"abc"`` made ``to_bytes()`` -- and ``diff()``, which writes both sides --
raise ``ValueError``. PHP overwrote column A with it and Node wrote a NaN column.
"""

from __future__ import annotations

import math
import re
from typing import Any

from ..workbook.cell_address import CellAddress

MAX_INDEX = 16383

_INDEX = re.compile(r"[0-9]{1,5}")
_WIDTH = re.compile(r"[0-9]+(\.[0-9]+)?")
_LETTERS = re.compile(r"[A-Za-z]{1,2}")


def index(key: Any) -> int | None:
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if 0 <= key <= MAX_INDEX else None
    if isinstance(key, str) and _INDEX.fullmatch(key) is not None:
        value = int(key)
        return value if value <= MAX_INDEX else None
    return None


def width(px: Any) -> float | None:
    if isinstance(px, bool):
        return None
    if isinstance(px, (int, float)):
        return float(px) if math.isfinite(px) and px >= 0 else None
    if isinstance(px, str) and _WIDTH.fullmatch(px) is not None:
        return float(px)
    return None


def from_letters(key: Any) -> int | None:
    """A one- or two-letter column key (``"B"``, ``"aa"``) as an index, for repair only.

    A longer run like ``"abc"`` is more likely a mistake than column ABC, and a
    repair must not guess.
    """
    if not isinstance(key, str) or _LETTERS.fullmatch(key.strip()) is None:
        return None
    return CellAddress.index(key.strip())
