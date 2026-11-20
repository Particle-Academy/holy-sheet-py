"""OOXML `.rels` parts -> {rId: {Target, Type, TargetMode}}, and target resolution.

The Type URI is kept so the caller can tell a comments relationship from a
drawing one; the TargetMode so an External relationship (a URI, never a part)
is never read as one.

Targets resolve per OPC (ECMA-376 Part 2): against the directory of the part
that OWNS the relationship, unless the target starts with "/", which makes it
package-absolute. Excel writes relative targets; openpyxl, LibreOffice and
others write absolute ones, and both are normal. Mirrors PHP `RelsParser`.
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
                "TargetMode": "External" if attr(rel, "TargetMode") == "External" else "Internal",
            }
        return rels

    @staticmethod
    def by_type(
        rels: dict[str, dict[str, str]], type_uri_contains: str
    ) -> dict[str, dict[str, str]]:
        """The INTERNAL relationships whose Type URI contains the fragment.
        External ones are left out: they name a URI, not a part."""
        return {
            rel_id: rel
            for rel_id, rel in rels.items()
            if type_uri_contains in rel["Type"] and not RelsParser.is_external(rel)
        }

    @staticmethod
    def is_external(rel: dict[str, str]) -> bool:
        return rel.get("TargetMode", "Internal") == "External"

    @staticmethod
    def resolve(source: str, target: str) -> str:
        """The part name `target` names, from the part `source` that owns the
        relationship ("" for the package root). "." and ".." normalise; ".."
        never climbs above the package root."""
        if target.startswith("/"):
            combined = target
        else:
            base_dir = source.rsplit("/", 1)[0] if "/" in source else ""
            combined = f"{base_dir}/{target}"
        parts: list[str] = []
        for segment in combined.split("/"):
            if segment == "..":
                if parts:
                    parts.pop()
            elif segment not in ("", "."):
                parts.append(segment)
        return "/".join(parts)

    @staticmethod
    def rels_path_of(part: str) -> str:
        """xl/worksheets/data.xml -> xl/worksheets/_rels/data.xml.rels."""
        directory, slash, base = part.rpartition("/")
        return f"{directory}/_rels/{base}.rels" if slash else f"_rels/{part}.rels"
