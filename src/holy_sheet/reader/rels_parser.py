"""OOXML `.rels` parts -> {rId: {Target, Type}}.

Used to resolve `xl/_rels/workbook.xml.rels` (rId -> worksheet path) and
`xl/worksheets/_rels/sheetN.xml.rels` (rId -> comments / vmlDrawing). The Type
URI is kept alongside the Target so the caller can tell a comments relationship
from a drawing one.
"""

from __future__ import annotations

from .xml import attr, find_all, parse_xml_or_none


class RelsParser:
    @staticmethod
    def parse(rels_xml: bytes | str | None) -> dict[str, dict[str, str]]:
        root = parse_xml_or_none(rels_xml)
        if root is None:
            return {}

        rels: dict[str, dict[str, str]] = {}
        for rel in find_all(root, "Relationship"):
            rel_id = attr(rel, "Id") or ""
            rels[rel_id] = {
                "Target": attr(rel, "Target") or "",
                "Type": attr(rel, "Type") or "",
            }
        return rels

    @staticmethod
    def by_type(
        rels: dict[str, dict[str, str]], type_uri_contains: str
    ) -> dict[str, dict[str, str]]:
        return {
            rel_id: rel
            for rel_id, rel in rels.items()
            if type_uri_contains in rel["Type"]
        }
