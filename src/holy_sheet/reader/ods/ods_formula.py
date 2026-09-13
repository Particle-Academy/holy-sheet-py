"""An OpenDocument `table:formula` -> the A1 syntax the xlsx reader returns.

Mirrors PHP `Reader\\Ods\\OdsFormula`, where the full list of what is and is not
translated is documented.

`of:=SUM([.A1:.B2];[$'Other Sheet'.C3])` becomes `SUM(A1:B2,'Other Sheet'!C3)`.
"""

from __future__ import annotations

import re

_GRAMMAR = re.compile(r"(of|oooc|msoxl):", re.IGNORECASE | re.ASCII)
_ADDRESS = re.compile(r"\$?[A-Za-z]{1,3}\$?[0-9]+|\$?[A-Za-z]{1,3}|\$?[0-9]+", re.ASCII)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*", re.ASCII)
_CELL_LIKE = re.compile(r"[A-Za-z]{1,3}[0-9]+", re.ASCII)
_R1C1_LIKE = re.compile(r"[Rr]([0-9]+)?[Cc]([0-9]+)?", re.ASCII)


def ods_formula_to_a1(formula: str) -> str:
    grammar = "of"
    match = _GRAMMAR.match(formula)
    if match:
        grammar = match.group(1).lower()
        formula = formula[match.end():]
    if formula.startswith("="):
        formula = formula[1:]

    return formula if grammar == "msoxl" else _translate(formula)


def _translate(s: str) -> str:
    out: list[str] = []
    length = len(s)
    array_depth = 0
    i = 0

    while i < length:
        c = s[i]

        if c == '"':
            end = i + 1
            while end < length:
                if s[end] == '"':
                    if end + 1 < length and s[end + 1] == '"':
                        end += 2
                        continue
                    break
                end += 1
            out.append(s[i:end + 1])
            i = end + 1
            continue

        if c == "[":
            end = i + 1
            quoted = False
            while end < length and (quoted or s[end] != "]"):
                if s[end] == "'":
                    quoted = not quoted
                end += 1
            out.append(_reference(s[i + 1:end]))
            i = end + 1
            continue

        if c == "{":
            array_depth += 1
        elif c == "}":
            array_depth = max(0, array_depth - 1)
        elif c == ";":
            c = ","
        elif c == "|" and array_depth > 0:
            c = ";"
        out.append(c)
        i += 1

    return "".join(out)


def _reference(ref: str) -> str:
    """The inside of one `[...]`."""
    parts: list[str] = []
    start = 0
    quoted = False
    for i, ch in enumerate(ref):
        if ch == "'":
            quoted = not quoted
        elif ch == ":" and not quoted:
            parts.append(ref[start:i])
            start = i + 1
    parts.append(ref[start:])

    parsed = [_part(p) for p in parts] if len(parts) <= 2 else [None]
    if any(p is None for p in parsed):
        return "#REF!" if "#REF!" in ref else f"[{ref}]"

    sheet, address = parsed[0]  # type: ignore[misc]
    if len(parsed) == 1:
        return _sheet_prefix(sheet) + address

    sheet2, address2 = parsed[1]  # type: ignore[misc]
    if sheet2 is None or sheet2 == sheet:
        return _sheet_prefix(sheet) + address + ":" + address2
    if sheet is None:
        return address + ":" + _sheet_prefix(sheet2) + address2

    # A range across sheets: one prefix naming both, quoted as a whole.
    if _needs_quotes(sheet) or _needs_quotes(sheet2):
        both = "'" + sheet.replace("'", "''") + ":" + sheet2.replace("'", "''") + "'"
    else:
        both = sheet + ":" + sheet2
    return both + "!" + address + ":" + address2


def _part(p: str) -> tuple[str | None, str] | None:
    """One end of a reference: `$'Sheet'.$A$1`, `Sheet.A1`, `.A1`, `.A`, `.1`."""
    length = len(p)
    i = 0
    if i < length and p[i] == "$":
        i += 1

    sheet: str | None = None
    if i < length and p[i] == "'":
        name: list[str] = []
        i += 1
        while True:
            if i >= length:
                return None
            if p[i] == "'":
                if i + 1 < length and p[i + 1] == "'":
                    name.append("'")
                    i += 2
                    continue
                i += 1
                break
            name.append(p[i])
            i += 1
        sheet = "".join(name)
        if i >= length or p[i] != ".":
            return None
        i += 1
    else:
        dot = p.find(".", i)
        if dot < 0:
            return None
        if dot > i:
            sheet = p[i:dot]
        i = dot + 1

    address = p[i:]
    if _ADDRESS.fullmatch(address) is None:
        return None
    return sheet, address


def _sheet_prefix(sheet: str | None) -> str:
    if sheet is None:
        return ""
    return ("'" + sheet.replace("'", "''") + "'" if _needs_quotes(sheet) else sheet) + "!"


def _needs_quotes(sheet: str) -> bool:
    """Whether Excel needs the sheet name quoted. Over-quoting is still valid."""
    return (
        _IDENTIFIER.fullmatch(sheet) is None
        or _CELL_LIKE.fullmatch(sheet) is not None
        or _R1C1_LIKE.fullmatch(sheet) is not None
    )
