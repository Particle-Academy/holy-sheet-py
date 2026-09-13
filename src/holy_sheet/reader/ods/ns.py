"""OpenDocument namespace URIs, lookups by URI, and the PHP string semantics the
reference ODS reader relies on. Mirrors PHP `Reader\\Ods\\Ns`.

Elements and attributes are matched by URI, never by prefix: `table:` is a
convention every producer happens to follow, not something the format promises.
`xml.etree` reports both as `{uri}local`, so a lookup here is a dict key.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
NUMBER = "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"
DC = "http://purl.org/dc/elements/1.1/"
META = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
#: LibreOffice's extension namespace; carries `value-type="error"` for a formula error.
CALCEXT = "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"


def qn(ns: str, name: str) -> str:
    """`{uri}local`, the way `xml.etree` spells a namespaced tag or attribute."""
    return f"{{{ns}}}{name}"


def is_el(element: Element, ns: str, name: str) -> bool:
    return element.tag == qn(ns, name)


def child(element: Element | None, ns: str, name: str) -> Element | None:
    """The first child element with this namespace and local name."""
    if element is None:
        return None
    tag = qn(ns, name)
    for c in element:
        if c.tag == tag:
            return c
    return None


def children_in(element: Element | None, ns: str) -> list[Element]:
    """Child elements in this namespace, in document order."""
    if element is None:
        return []
    prefix = f"{{{ns}}}"
    return [c for c in element if isinstance(c.tag, str) and c.tag.startswith(prefix)]


def local(element: Element) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def attr(element: Element | None, ns: str, name: str) -> str | None:
    """A namespaced attribute, or None when absent."""
    if element is None:
        return None
    return element.attrib.get(qn(ns, name))


def own_text(element: Element | None) -> str:
    """An element's own text runs, as SimpleXML's `(string)` cast reads them."""
    if element is None:
        return ""
    return (element.text or "") + "".join(c.tail or "" for c in element)


# --- PHP semantics ---------------------------------------------------------
#
# The PHP reader is the reference, and several of its primitives are narrower
# than their Python look-alikes: `trim` and `strtolower` are ASCII-only, `\d`
# and `\b` in its patterns are ASCII, and `(int)` reads a leading integer or 0.
# `str.strip()` would agree on every fixture and disagree on the first file with
# a narrow no-break space in a currency format.

_PHP_TRIM = " \t\n\r\0\x0b"
_LEADING_INT = re.compile(r"[ \t\n\r\v\f]*([+-]?[0-9]+)", re.ASCII)
_PHP_NUMERIC = re.compile(
    r"[ \t\n\r\v\f]*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?[ \t\n\r\v\f]*", re.ASCII
)


def php_trim(value: str) -> str:
    return value.strip(_PHP_TRIM)


def ascii_lower(value: str) -> str:
    return re.sub(r"[A-Z]", lambda m: m.group(0).lower(), value)


def php_int(value: str) -> int:
    """PHP `(int) $string`: the leading integer, 0 when there is none."""
    match = _LEADING_INT.match(value)
    return int(match.group(1)) if match else 0


def php_is_numeric(value: str) -> bool:
    """PHP 8 `is_numeric()` on a string."""
    return _PHP_NUMERIC.fullmatch(value) is not None
