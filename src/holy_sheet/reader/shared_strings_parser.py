"""`xl/sharedStrings.xml` -> a list of strings indexed by shared-string index.

This writer never emits a shared-strings table (it writes inline strings), but
Excel and every other authoring tool do, and a `t="s"` cell is meaningless
without it. Rich strings -- an `<si>` holding several `<r><t>` runs -- are
flattened to plain text, because the cell-value model here is plain text and
run-level formatting has nowhere to go.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element

if TYPE_CHECKING:
    from .read_limits import ReadLimits

from .xml import find, find_all, parse_xml_or_none, text_of


class SharedStringsParser:
    @staticmethod
    def parse(xml: bytes | str | None, limits: "ReadLimits | None" = None) -> list[str]:
        """The table is counted before any entry is rendered (``maxSharedStrings``)."""
        root = parse_xml_or_none(xml)
        if root is None:
            return []
        items = find_all(root, "si")
        if limits is not None:
            limits.shared_strings(len(items))
        return [render_si(si) for si in items]


def render_si(si: Element) -> str:
    """One string item's text: its `<t>`, or its `<r><t>` runs joined. Also how
    a cell's inline `<is>` is read, which has the same content model."""
    direct = find(si, "t")
    if direct is not None:
        return text_of(direct)
    return "".join(text_of(find(run, "t")) for run in find_all(si, "r"))
