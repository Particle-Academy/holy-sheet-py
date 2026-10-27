"""Office and LibreOffice TEMPLATE packages: `.xltx` / `.ots` for this package.

Mirrors PHP `HolySheet\\Archive\\OfficeTemplate` and Node `zip/office-template.ts`
(same family table, reason codes and byte layout of a converted archive).

A template is its document twin with a different declared type, nothing more:

- OOXML (ECMA-376 Part 2 content types): the main part's Override in
  `[Content_Types].xml` reads `...spreadsheetml.template.main+xml` instead of
  `...sheet.main+xml`.
- ODF (1.3 Part 2 section 3.3): the first, STORED `mimetype` entry reads
  `...opendocument.spreadsheet-template`, and that value SHALL equal the
  `manifest:media-type` of the manifest's root (`/`) entry. Rewriting the
  `mimetype` alone would produce a package that contradicts itself.

So reading one is classifying it, and converting one is rewriting those
declarations while every other member is copied as its original raw zip record:
same compressed bytes, same CRC, same timestamps.

Declarations are edited as latin-1 strings (one char per byte) so the bytes
around the edited value survive exactly, whatever their encoding.

Macro-enabled templates (`.dotm`, `.potm`, `.xltm`) are refused, never read
around. Every family is listed so a template declaring ANOTHER family is
recognised and refused rather than read as whatever the sniffer guessed.
"""

from __future__ import annotations

import re
import struct
import zlib
from typing import Any, Iterator, Protocol
from xml.etree.ElementTree import Element

from ..exceptions import OfficeTemplateRefused
from .source_archive import SourceArchive


def parse_xml_or_none(data: bytes | None) -> Element | None:
    """The reader's DOCTYPE-refusing parser, imported late: the readers import
    this module, so importing the reader package at load time is a cycle."""
    from ..reader.xml import parse_xml_or_none as parse

    return parse(data)

DOCUMENT = "document"
TEMPLATE = "template"

#: The family this package reads and converts.
FAMILY = "spreadsheet"
#: Whether this package reads (and so converts) the family's ODF form.
ODF = True

_VBA = "application/vnd.ms-office.vbaProject"

FAMILIES: dict[str, dict[str, str]] = {
    "word": {
        "part": "/word/document.xml",
        "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "template": "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        "macroDocument": "application/vnd.ms-word.document.macroEnabled.main+xml",
        "macroTemplate": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
        "odfDocument": "application/vnd.oasis.opendocument.text",
        "odfTemplate": "application/vnd.oasis.opendocument.text-template",
        "odfBody": "text",
    },
    "presentation": {
        "part": "/ppt/presentation.xml",
        "document": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "template": "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        "macroDocument": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
        "macroTemplate": "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
        "odfDocument": "application/vnd.oasis.opendocument.presentation",
        "odfTemplate": "application/vnd.oasis.opendocument.presentation-template",
        "odfBody": "presentation",
    },
    "spreadsheet": {
        "part": "/xl/workbook.xml",
        "document": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "template": "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
        "macroDocument": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
        "macroTemplate": "application/vnd.ms-excel.template.macroEnabled.main+xml",
        "odfDocument": "application/vnd.oasis.opendocument.spreadsheet",
        "odfTemplate": "application/vnd.oasis.opendocument.spreadsheet-template",
        "odfBody": "spreadsheet",
    },
}

_OWN = FAMILIES[FAMILY]


class PackageView(Protocol):
    """What classification needs: a verified SourceArchive does."""

    def read(self, name: str) -> bytes | None: ...

    def names(self) -> list[str]: ...


def source_kind(archive: PackageView) -> str:
    """`document` or `template` for a package this package's reader is about to
    read. A package that declares nothing a template would is a `document`,
    exactly as before: only templates are new.

    Raises `OfficeTemplateRefused` (`macro_enabled` or `family_mismatch`).
    """
    types = archive.read("[Content_Types].xml")
    if types is not None:
        return _ooxml_kind(archive, types)
    mimetype = archive.read("mimetype")
    if mimetype is not None:
        return _odf_kind(archive, mimetype.decode("latin-1"))
    return DOCUMENT


def convert(data: bytes, to: str | None = None) -> bytes:
    """Turn a template into its document twin, or a document into its template.
    `to` None flips whichever it is.

    Raises `OfficeTemplateRefused`, or `ArchiveRefused` through the archive guard.
    """
    if to is not None and to not in (DOCUMENT, TEMPLATE):
        raise OfficeTemplateRefused("bad_target")
    archive = SourceArchive.open(data)
    kind = source_kind(archive)
    target = to if to is not None else (DOCUMENT if kind == TEMPLATE else TEMPLATE)
    if target == kind:
        raise OfficeTemplateRefused(f"already_{kind}")

    replacements: dict[str, bytes] = {}
    types = archive.read("[Content_Types].xml")
    mimetype = archive.read("mimetype")
    if types is not None:
        declared = _declarations(types)
        if declared is None:
            raise OfficeTemplateRefused("not_convertible")
        if _has_vba(archive, declared) or _OWN["macroDocument"] in _types_of(declared, _OWN["part"]):
            raise OfficeTemplateRefused("macro_enabled")
        source, value = (_OWN["document"], _OWN["template"]) if target == TEMPLATE else (_OWN["template"], _OWN["document"])
        rewritten = _rewrite_override(types.decode("latin-1"), _OWN["part"], source, value)
        replacements["[Content_Types].xml"] = rewritten.encode("latin-1")
    elif ODF and mimetype is not None:
        # First in the central directory AND first in the file, stored, no extra
        # field: ODF 1.3 Part 2 section 3.3.
        entries = archive.entries()
        first = entries[0] if entries else None
        by_local = sorted(entries, key=lambda entry: entry["local"])
        if (
            first is None
            or first["name"] != "mimetype"
            or by_local[0]["name"] != "mimetype"
            or first["method"] != 0
            or first["local_extra"] != 0
        ):
            raise OfficeTemplateRefused("odf_mimetype")
        source, value = (
            (_OWN["odfDocument"], _OWN["odfTemplate"]) if target == TEMPLATE else (_OWN["odfTemplate"], _OWN["odfDocument"])
        )
        if mimetype.decode("latin-1") != source:
            raise OfficeTemplateRefused("not_convertible")
        replacements["mimetype"] = value.encode("latin-1")
        manifest = archive.read("META-INF/manifest.xml")
        if manifest is not None:
            moved = _rewrite_manifest_root(manifest.decode("latin-1"), source, value)
            if moved is not None:
                replacements["META-INF/manifest.xml"] = moved.encode("latin-1")
    else:
        raise OfficeTemplateRefused("not_convertible")

    out = _rebuild(archive, replacements)
    if source_kind(SourceArchive.open(out)) != target:
        raise RuntimeError("Template conversion did not produce the requested kind.")
    return out


# ─── Classification ──────────────────────────────────────────────────────


def _ooxml_kind(archive: PackageView, types: bytes) -> str:
    declared = _declarations(types)
    # Unparsable content types were never looked at before; the reader's own
    # handling of the package is unchanged.
    if declared is None:
        return DOCUMENT
    kind = DOCUMENT
    for part, content_type in declared:
        for name, family in FAMILIES.items():
            if content_type not in (family["template"], family["macroTemplate"]):
                continue
            # A template type on a Default, on another part, or of another family.
            if name != FAMILY or part != _ascii_lower(_OWN["part"]):
                raise OfficeTemplateRefused("family_mismatch")
            if content_type == family["macroTemplate"]:
                raise OfficeTemplateRefused("macro_enabled")
            kind = TEMPLATE
    if kind == TEMPLATE and _has_vba(archive, declared):
        raise OfficeTemplateRefused("macro_enabled")
    return kind


def _odf_kind(archive: PackageView, mimetype: str) -> str:
    if mimetype != _OWN["odfTemplate"]:
        if mimetype.startswith("application/vnd.oasis.opendocument.") and mimetype.endswith("-template"):
            raise OfficeTemplateRefused("family_mismatch")
        return DOCUMENT
    content = parse_xml_or_none(archive.read("content.xml"))
    body = _first_by_local_name(content, "body") if content is not None else None
    first = next(iter(body), None) if body is not None else None
    if first is None or _local(first.tag) != _OWN["odfBody"]:
        raise OfficeTemplateRefused("family_mismatch")
    root = _manifest_root_type(parse_xml_or_none(archive.read("META-INF/manifest.xml")))
    if root is not None and root not in (_OWN["odfTemplate"], _OWN["odfDocument"]):
        raise OfficeTemplateRefused("family_mismatch")
    return TEMPLATE


def _declarations(types: bytes | str) -> list[tuple[str, str]] | None:
    """Every content-type declaration as (ASCII-lowercased part or `*.ext`, type), by local name."""
    root = parse_xml_or_none(types.encode("latin-1") if isinstance(types, str) else types)
    if root is None:
        return None
    out: list[tuple[str, str]] = []
    for el in _elements(root):
        local = _local(el.tag)
        if local == "Override":
            out.append((_ascii_lower(_attr(el, "PartName") or ""), _attr(el, "ContentType") or ""))
        elif local == "Default":
            out.append(("*." + _ascii_lower(_attr(el, "Extension") or ""), _attr(el, "ContentType") or ""))
    return out


def _types_of(declared: list[tuple[str, str]], part: str) -> list[str]:
    return [content_type for name, content_type in declared if name == _ascii_lower(part)]


_VBA_NAME = re.compile(r"(^|/)vbaProject\.bin$", re.IGNORECASE)


def _has_vba(archive: PackageView, declared: list[tuple[str, str]]) -> bool:
    if any(_ascii_lower(content_type) == _ascii_lower(_VBA) for _, content_type in declared):
        return True
    return any(_VBA_NAME.search(name) for name in archive.names())


def _manifest_root_type(root: Element | None) -> str | None:
    if root is None:
        return None
    for el in _elements(root):
        if _local(el.tag) == "file-entry" and _attr(el, "full-path") == "/":
            return _attr(el, "media-type")
    return None


# ─── Rewriting ───────────────────────────────────────────────────────────

_OVERRIDE_TAG = re.compile(r"<(?:[A-Za-z_][\w.\-]*:)?Override\b[^>]*>", re.ASCII)
_PART_NAME = re.compile(r"\sPartName\s*=\s*([\"'])(.*?)\1", re.ASCII | re.DOTALL)
_CONTENT_TYPE = re.compile(r"\sContentType\s*=\s*([\"'])(.*?)\1", re.ASCII | re.DOTALL)
_FILE_ENTRY_TAG = re.compile(r"<(?:[A-Za-z_][\w.\-]*:)?file-entry\b[^>]*>", re.ASCII)
_ROOT_PATH = re.compile(r"\s(?:[A-Za-z_][\w.\-]*:)?full-path\s*=\s*([\"'])/\1", re.ASCII)
_MEDIA_TYPE = re.compile(r"\s(?:[A-Za-z_][\w.\-]*:)?media-type\s*=\s*([\"'])(.*?)\1", re.ASCII | re.DOTALL)


def _rewrite_override(xml: str, part: str, source: str, value: str) -> str:
    """Replace one attribute VALUE in place. A text scan finds the candidate; the
    parser then has to agree on the result, which is what stops a decoy in a
    comment or an entity-encoded name from steering the edit."""
    hits: list[int] = []
    for tag in _OVERRIDE_TAG.finditer(xml):
        name = _PART_NAME.search(tag.group(0))
        if name is None or _ascii_lower(name.group(2)) != _ascii_lower(part):
            continue
        content_type = _CONTENT_TYPE.search(tag.group(0))
        if content_type is not None and content_type.group(2) == source:
            hits.append(tag.start() + content_type.start(2))
    if len(hits) != 1:
        raise OfficeTemplateRefused("not_convertible")
    out = xml[: hits[0]] + value + xml[hits[0] + len(source) :]
    declared = _declarations(out)
    if declared is None or _types_of(declared, part) != [value]:
        raise OfficeTemplateRefused("not_convertible")
    return out


def _rewrite_manifest_root(xml: str, source: str, value: str) -> str | None:
    """The manifest with its root entry's media-type moved; None when it has no root entry."""
    current = _manifest_root_type(parse_xml_or_none(xml.encode("latin-1")))
    if current is None:
        return None
    if current != source:
        raise OfficeTemplateRefused("not_convertible")
    hits: list[int] = []
    for tag in _FILE_ENTRY_TAG.finditer(xml):
        if _ROOT_PATH.search(tag.group(0)) is None:
            continue
        media_type = _MEDIA_TYPE.search(tag.group(0))
        if media_type is not None and media_type.group(2) == source:
            hits.append(tag.start() + media_type.start(2))
    if len(hits) != 1:
        raise OfficeTemplateRefused("not_convertible")
    out = xml[: hits[0]] + value + xml[hits[0] + len(source) :]
    if _manifest_root_type(parse_xml_or_none(out.encode("latin-1"))) != value:
        raise OfficeTemplateRefused("not_convertible")
    return out


def _rebuild(archive: SourceArchive, replacements: dict[str, bytes]) -> bytes:
    """Re-assemble the archive. Untouched members are their original local
    records, verbatim; a replaced member is written STORED with its original
    timestamp and no extra field (which is what ODF requires of `mimetype`).
    Local order and central order are both kept."""
    original = archive.to_bytes()
    entries = archive.entries()

    def u16(at: int) -> int:
        return struct.unpack_from("<H", original, at)[0]

    def u32(at: int) -> int:
        return struct.unpack_from("<I", original, at)[0]

    def name_bytes(entry: dict[str, Any]) -> bytes:
        return original[entry["central"] + 46 : entry["central"] + 46 + u16(entry["central"] + 28)]

    locals_ = bytearray()
    offsets: dict[str, int] = {}
    for entry in sorted(entries, key=lambda item: item["local"]):
        offsets[entry["name"]] = len(locals_)
        data = replacements.get(entry["name"])
        if data is None:
            locals_ += original[entry["local"] : entry["finish"]]
            continue
        name = name_bytes(entry)
        locals_ += struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 10, entry["flags"] & 0x0800, 0,
            u16(entry["local"] + 10), u16(entry["local"] + 12),
            zlib.crc32(data), len(data), len(data), len(name), 0,
        ) + name + data

    central = bytearray()
    for entry in entries:
        data = replacements.get(entry["name"])
        at = entry["central"]
        if data is None:
            record = bytearray(original[at : at + entry["central_length"]])
            struct.pack_into("<I", record, 42, offsets[entry["name"]])
            central += record
            continue
        name = name_bytes(entry)
        central += struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, u16(at + 4), 10, entry["flags"] & 0x0800, 0,
            u16(at + 12), u16(at + 14), zlib.crc32(data), len(data), len(data),
            len(name), 0, 0, 0, u16(at + 36), u32(at + 38), offsets[entry["name"]],
        ) + name

    comment = archive.comment()
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(locals_), len(comment))
    return bytes(locals_) + bytes(central) + end + comment


# ─── Helpers ─────────────────────────────────────────────────────────────


def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(el: Element, local: str) -> str | None:
    """An attribute by local name, whatever its namespace."""
    for key, value in el.attrib.items():
        if key == local or key.endswith("}" + local):
            return value
    return None


def _elements(root: Element) -> Iterator[Element]:
    return root.iter()


def _first_by_local_name(root: Element, local: str) -> Element | None:
    for el in root.iter():
        if _local(el.tag) == local:
            return el
    return None


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_LOWER)


_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}
