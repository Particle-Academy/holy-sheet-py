"""XML escaping, matching the PHP writer character for character.

The writers in this family are STRING BUILDERS, not DOM serialisers, and that
is the contract rather than an implementation detail: attribute order,
self-closing style, the absence of inter-element whitespace and `&apos;` vs
`&#39;` are all observable in the part bytes the parity suite diffs. An
off-the-shelf serialiser owns every one of those decisions and would break the
first fixture.
"""

from __future__ import annotations

import re

# PHP: /[\x00-\x08\x0B\x0C\x0E-\x1F]/u -- the control characters XML 1.0 does
# not permit at all, removed rather than escaped (there is no legal escape).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def xml_escape(text: str) -> str:
    """The writer's ``escape()``: strip control characters, then escape.

    ``htmlspecialchars($s, ENT_XML1 | ENT_QUOTES, 'UTF-8')`` -- all five XML
    metacharacters, with the apostrophe as ``&apos;`` (Go's ``encoding/xml``
    writes ``&#39;`` and would fail parts parity on the first fixture).
    ``&`` must be replaced first or the other replacements double-escape.
    """
    return xml_escape_raw(_CONTROL_CHARS.sub("", text))


def xml_escape_raw(text: str) -> str:
    """Escape only -- no control-character strip.

    This exists because `StylesRegistry` calls bare ``htmlspecialchars`` where
    the writer calls its own ``escape()``, so number-format codes, font names
    and alignment values skip the strip that cell text gets. That asymmetry is
    a real (harmless) inconsistency in the reference engine, recorded in the
    parity spec; it is mirrored rather than tidied, because tidying it here
    would change bytes that both shipped engines currently agree on.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
