"""The text of a cell or annotation: its paragraphs, one line each.

Mirrors PHP `Reader\\Ods\\OdsText`. A paragraph is mixed content -- runs, links,
`text:s` for extra spaces, `text:tab`, `text:line-break` -- and its meaning
depends on ORDER, so it is walked through `.text` and each child's `.tail`.
Inside a paragraph, elements are matched by local name, as the PHP reader
matches them.

White space follows ODF 1.3 section 6.1.2, the way LibreOffice applies it: a run
of space, tab, CR and LF characters is ONE space, and white space at the start of
a paragraph or straight after another collapsed space is dropped. `text:s` is
never collapsed.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

from ..xml import attr as local_attr
from . import ns

#: Elements whose text is not part of the paragraph (a note, a comment).
_SKIPPED = frozenset({"annotation", "note"})
_DIGITS = re.compile(r"[0-9]+", re.ASCII)
_WHITESPACE = frozenset(" \t\n\r")


class _State:
    __slots__ = ("out", "ignore_space")

    def __init__(self) -> None:
        self.out: list[str] = []
        self.ignore_space = True


def paragraphs(element: Element) -> str | None:
    """The `text:p` / `text:h` children joined with "\\n", or None when there are none."""
    lines = [
        paragraph(c)
        for c in element
        if c.tag in (ns.qn(ns.TEXT, "p"), ns.qn(ns.TEXT, "h"))
    ]
    return None if not lines else "\n".join(lines)


def paragraph(p: Element) -> str:
    state = _State()
    _walk(p, state)
    return "".join(state.out)


def _walk(node: Element, state: _State) -> None:
    if node.text:
        _append(state, node.text)
    for c in node:
        if isinstance(c.tag, str):
            name = ns.local(c)
            if name not in _SKIPPED:
                if name == "s":
                    count_attr = local_attr(c, "c")
                    count = int(count_attr) if count_attr is not None and _DIGITS.fullmatch(count_attr) else 1
                    state.out.append(" " * max(1, count))
                    state.ignore_space = False
                elif name == "tab":
                    state.out.append("\t")
                    state.ignore_space = False
                elif name == "line-break":
                    state.out.append("\n")
                    state.ignore_space = False
                _walk(c, state)
        if c.tail:
            _append(state, c.tail)


def _append(state: _State, chars: str) -> None:
    for ch in chars:
        if ch in _WHITESPACE:
            if not state.ignore_space:
                state.out.append(" ")
                state.ignore_space = True
            continue
        state.out.append(ch)
        state.ignore_space = False
