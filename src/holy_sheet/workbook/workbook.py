"""The canonical workbook value object.

Every input shape -- row-oriented, sparse `cells`, fancy-sheets passthrough --
is normalized into a `Workbook` before the writer sees anything. That is what
keeps the writer dumb: it walks Workbook -> xlsx and never learns about
alternative input shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .sheet import Sheet


@dataclass
class Workbook:
    sheets: list[Sheet] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
