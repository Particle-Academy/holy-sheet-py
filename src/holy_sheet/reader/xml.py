"""Safe XML parsing for the read path.

Reading is the one half of an OOXML engine where an off-the-shelf parser is the
RIGHT answer -- nothing is serialised, only walked -- so `xml.etree` does the
work here while the writer stays a string builder.

Two things this wrapper adds:

1. **A DOCTYPE is rejected before parsing.** A legitimate xlsx never contains
   one, and an internal DTD subset is the entry point for entity expansion
   ("billion laughs") and for external-entity file reads. `xml.etree` does not
   expand external entities, but it does process an internal subset, and a
   reader that is handed arbitrary uploaded files should not find out what its
   parser does with one. Refusing the construct outright is cheaper and does not
   depend on a parser's configuration staying the way it is today.
2. **Namespace-blind lookups.** OOXML parts bind the same namespaces under
   whatever prefixes the producer felt like, and `xml.etree` reports tags as
   `{uri}local`. Matching on the local name is what the Node reader does too,
   and it is what makes a part written by Excel, LibreOffice or this package all
   read the same.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

_DOCTYPE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)


class XmlError(ValueError):
    """Raised when a part cannot be parsed, or refuses to be parsed."""


def parse_xml(data: bytes | str) -> ElementTree.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if _DOCTYPE.search(raw[:4096]):
        raise XmlError(
            "[holy-sheet] refusing to parse XML containing a DOCTYPE declaration; "
            "an xlsx part never legitimately has one."
        )
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise XmlError(f"[holy-sheet] malformed XML: {error}") from error


def parse_xml_or_none(data: bytes | str | None) -> ElementTree.Element | None:
    """Parse, or None when the part is absent or unparseable.

    Mirrors the reference engine's `@simplexml_load_string(...) === false`
    branches, which degrade to an empty result rather than throwing.
    """
    if data is None:
        return None
    try:
        return parse_xml(data)
    except XmlError:
        return None


def local_name(tag: str) -> str:
    """`{http://…}worksheet` -> `worksheet`."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def find(element: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    """First direct child with this local name."""
    if element is None:
        return None
    for child in element:
        if local_name(child.tag) == name:
            return child
    return None


def find_all(element: ElementTree.Element | None, name: str) -> list[ElementTree.Element]:
    """Every direct child with this local name, in document order."""
    if element is None:
        return []
    return [child for child in element if local_name(child.tag) == name]


def attr(element: ElementTree.Element | None, name: str) -> str | None:
    """Attribute by local name, ignoring whatever prefix bound it."""
    if element is None:
        return None
    if name in element.attrib:
        return element.attrib[name]
    for key, value in element.attrib.items():
        if local_name(key) == name:
            return value
    return None


def text_of(element: ElementTree.Element | None) -> str:
    """All descendant text, concatenated -- SimpleXML's `(string)` cast."""
    if element is None:
        return ""
    return "".join(element.itertext())
