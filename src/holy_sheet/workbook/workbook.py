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
    #: What a reader found: an .xltx / .ots reads exactly like its .xlsx / .ods
    #: twin and is marked "template".
    source_kind: str = "document"
    #: Whether the file counts its date serials from 1904-01-01
    #: (`<workbookPr date1904="1"/>`). Read only: the writer always writes the
    #: 1900 system, and describe() turns every date into an ISO string.
    date1904: bool = False
    #: The sheet shown when the file opens, as an index into `sheets`; None
    #: when nothing names one. A reader sets it from the file (`<workbookView
    #: activeTab>`, or an ODS view's `ActiveTable`), the Normalizer from the
    #: schema's `activeSheet`. CC-fancy-0050.
    active_sheet: int | None = None
