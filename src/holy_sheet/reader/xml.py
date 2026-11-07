"""Safe XML parsing for the read path.

Reading is the one half of an OOXML engine where an off-the-shelf parser is the
RIGHT answer -- nothing is serialised, only walked -- so `xml.etree` does the
work here while the writer stays a string builder.

Two things this wrapper adds:

1. **A DOCTYPE is refused, and this is the only place a part gets parsed.** A
   legitimate spreadsheet part never has one, and an internal DTD subset is the
   entry point for entity expansion. The check has two halves. The prolog is
   read first, so an ordinary refusal never reaches the parser; it looks only
   at the prolog, because searching the whole part would refuse a real cell
   whose CDATA holds an HTML page. The parser then reports any doctype it meets,
   and that report is what decides: no byte scan reads every encoding, and the
   parse stops there, before the subset is processed. A part whose encoding the
   parser cannot read is refused too. A test fails if `src/` parses XML anywhere
   else. A DOCTYPE refusal raises `UnsupportedFormatException` out of
   `parse_xml_or_none`; malformed XML and an unreadable encoding still read as
   absent there.
2. **Namespace-blind lookups.** OOXML parts bind the same namespaces under
   whatever prefixes the producer felt like, and `xml.etree` reports tags as
   `{uri}local`. Matching on the local name is what the Node reader does too,
   and it is what makes a part written by Excel, LibreOffice or this package all
   read the same.
"""

from __future__ import annotations

from xml.etree import ElementTree

_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_DOCTYPE_REFUSED = (
    "[holy-sheet] refusing to parse XML containing a DOCTYPE declaration; "
    "a spreadsheet part never legitimately has one."
)


class XmlError(ValueError):
    """Raised when a part cannot be parsed, or refuses to be parsed."""


class XmlRefused(XmlError):
    """The part carries a DOCTYPE, or an encoding the parser cannot read.

    A malformed part is only an `XmlError`. Most callers treat the two alike;
    the package declaration is the one place the difference decides anything.
    """


class XmlDoctypeRefused(XmlRefused):
    """The part carries a DOCTYPE: the refusal that refuses the whole file.

    An unreadable encoding is refused too, but it says nothing about what the
    part holds, so outside the package declaration it still reads as absent
    (as PHP, whose libxml decodes more encodings, reads one it cannot decode).
    """


class _DoctypeMet(Exception):
    pass


class _RefusingBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise _DoctypeMet


def prolog_declares_doctype(raw: bytes) -> bool:
    """Whether a DOCTYPE comes before the root element, for the encodings a byte
    walk can read: anything ASCII-compatible, and UTF-16 or UTF-32 once their
    NUL bytes are dropped (which keeps the ASCII this looks for)."""
    if b"\0" in raw[:4]:
        raw = raw.replace(b"\0", b"")
    at = 0
    for bom in _BOMS:
        if raw.startswith(bom):
            at = len(bom)
            break
    while True:
        while at < len(raw) and raw[at] in b" \t\r\n":
            at += 1
        if raw.startswith(b"<?", at):
            close = b"?>"
        elif raw.startswith(b"<!--", at):
            close = b"-->"
        else:
            return raw[at : at + 9].lower() == b"<!doctype"
        end = raw.find(close, at)
        if end == -1:
            return False
        at = end + len(close)


def parse_xml(data: bytes | str) -> ElementTree.Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if prolog_declares_doctype(raw):
        raise XmlDoctypeRefused(_DOCTYPE_REFUSED)
    parser = ElementTree.XMLParser(target=_RefusingBuilder())
    try:
        parser.feed(raw)
        return parser.close()
    except _DoctypeMet:
        raise XmlDoctypeRefused(_DOCTYPE_REFUSED) from None
    except ElementTree.ParseError as error:
        raise XmlError(f"[holy-sheet] malformed XML: {error}") from error
    except (ValueError, LookupError) as error:
        # The parser hands a declared encoding it cannot read straight up as one
        # of these, and it says nothing about what the part holds.
        raise XmlRefused(f"[holy-sheet] unreadable XML encoding: {error}") from error


def is_refused(data: bytes) -> bool:
    """Whether a part is refused, as opposed to merely malformed or fine."""
    try:
        parse_xml(data)
    except XmlRefused:
        return True
    except XmlError:
        return False
    return False


def parse_xml_or_none(data: bytes | str | None) -> ElementTree.Element | None:
    """Parse, or None when the part is absent or unparseable.

    Mirrors the reference engine's `PartXml::simple(...) === false` branches,
    which degrade to an empty result rather than throwing -- EXCEPT for a
    DOCTYPE, which raises `UnsupportedFormatException` (PHP 2.11.5). Read as
    absent, a worksheet carrying one came back as an empty sheet, and the
    caller could not tell that from a sheet with nothing on it.
    """
    if data is None:
        return None
    try:
        return parse_xml(data)
    except XmlDoctypeRefused:
        from ..exceptions import UnsupportedFormatException

        raise UnsupportedFormatException.refused_part() from None
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
