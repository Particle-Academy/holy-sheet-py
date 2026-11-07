"""One portable table -> RFC 4180 CSV. A port of PHP `Exchange\\Csv::toCsv()`.

With the parts of PHP's `Display`, `Portable::layout` and `Report` it uses,
held to PHP's text and report call for call by `tests/test_csv_parity_php.py`.

Formula-injection escaping is ON by default: a ``string`` or ``error`` field
beginning with ``=``, ``+``, ``-``, ``@``, TAB or CR is prefixed with ``'``
before it is quoted, and reported ``csv-escaped``. Numbers are never escaped.
Only the host turns it off (``{"escapeFormulas": False}``).

The input is portable content's table shape, ``{"rows": [{"cells": [{"t",
"v", "colSpan"?, "rowSpan"?, "text"?, "format"?}]}]}``. Options and report keys
are PHP's (camelCase), so the same JSON drives all three engines. Lines end
CRLF, every line including the last.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..helpers.php import format_float
from ..ops._php_array import php_int_cast


class OptionsInvalid(ValueError):
    """Bad options (``options_invalid``). Mirrors PHP `Exchange\\OptionsInvalid`."""

    CODE = "options_invalid"

    def __init__(self, message: str = "") -> None:
        super().__init__("[holy-sheet] options_invalid" + ("" if message == "" else ": " + message))


_LEADS = frozenset(["=", "+", "-", "@", "\t", "\r"])
_WHERE_CAP = 16
_NEEDS_QUOTES = re.compile(r'[",\r\n]')
_ZEROS = re.compile(r"[0.]+")
_THOUSANDS = re.compile(r"\B(?=(\d{3})+(?!\d))", re.ASCII)
_EXPONENT = re.compile(r"(\d+)(?:\.(\d+))?e([+-]?\d+)", re.ASCII | re.IGNORECASE)
_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "₹",
    "AUD": "A$", "CAD": "C$", "CHF": "CHF", "KRW": "₩",
}
_DEFAULT_DECIMALS = {"number": 0, "currency": 2, "percent": 1}


def to_csv(table: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """`{"text": ..., "report": {"dropped", "approximated", "guard"}}`, byte for byte as PHP."""
    opts = options or {}
    for key, value in opts.items():
        if key not in ("escapeFormulas", "display") or not isinstance(value, bool):
            raise OptionsInvalid(f"bad csv option {key}")
    escape = opts.get("escapeFormulas", True) is not False
    show_display = opts.get("display", False) is True

    approximated: dict[str, dict[str, Any]] = {}

    def add(kind: str, where: str) -> None:
        entry = approximated.setdefault(kind, {"count": 0, "where": []})
        entry["count"] += 1
        if len(entry["where"]) < _WHERE_CAP:
            entry["where"].append(where)

    raw_rows = table.get("rows") if isinstance(table, dict) else None
    rows = list(raw_rows.values()) if isinstance(raw_rows, dict) else list(raw_rows or [])
    g = _layout(rows)
    # A row's fields keep PHP's array order: a column first set past the
    # table's width is appended, which is what a ragged table gives in PHP.
    grid: list[dict[int, str]] = [{c: "" for c in range(g["width"])} for _ in rows]

    for ri, r in enumerate(rows):
        cells = r.get("cells") if isinstance(r, dict) else None
        if not isinstance(cells, (list, dict)):
            raise TypeError(f"[holy-sheet] csv: rows[{ri}] has no cells list")
        for ci, cell in enumerate(cells.values() if isinstance(cells, dict) else cells):
            cp = f"/rows/{ri}/cells/{ci}"
            anchors = g["anchors"]
            if ri >= len(anchors) or ci >= len(anchors[ri]):
                # PHP reads past its layout here (a warning, then a garbled row). Refuse instead.
                raise TypeError(f"[holy-sheet] csv: the table's rows do not form a grid at row {ri}")
            ar, ac = anchors[ri][ci]
            if _gt_one(_or_one(cell.get("colSpan"))) or _gt_one(_or_one(cell.get("rowSpan"))):
                add("merge-flattened", cp)
            t = _php_string(cell.get("t"))
            v = cell.get("v")
            if show_display:
                if isinstance(cell.get("text"), str):
                    f = cell["text"]
                elif cell.get("format") is not None:
                    f = _display(t, v, cell["format"])
                else:
                    f = _canonical(t, v)
            else:
                f = _canonical(t, v)
            if escape and t in ("string", "error") and f != "" and f[0] in _LEADS:
                f = "'" + f
                add("csv-escaped", cp)
            if _NEEDS_QUOTES.search(f):
                f = '"' + f.replace('"', '""') + '"'
            grid[ar][ac] = f

    text = "".join(",".join(line.values()) + "\r\n" for line in grid)
    return {
        "text": text,
        "report": {
            "dropped": [],
            # Sorted by kind in UTF-16 code-unit order, as PHP's Report sorts; both kinds are ASCII.
            "approximated": [
                {"kind": kind, "count": approximated[kind]["count"], "where": approximated[kind]["where"]}
                for kind in sorted(approximated)
            ],
            "guard": {"findings": []},
        },
    }


def _layout(rows: list[Any]) -> dict[str, Any]:
    """PHP `Portable::layout()`: where each cell's top-left lands, and the table's width."""
    covered: dict[int, set[int]] = {}
    anchors: list[list[tuple[int, int]]] = []
    width = -1
    n = len(rows)
    for r, row in enumerate(rows):
        occ = set(covered.get(r, set()))
        covered_count = len(occ)
        placed: list[tuple[int, int]] = []
        c = 0
        span = 0
        cells = row.get("cells") if isinstance(row, dict) else None
        cells = list(cells.values()) if isinstance(cells, dict) else (cells if isinstance(cells, list) else [])
        for cell in cells:
            while c in occ:
                c += 1
            # PHP's `(int) ($cell['colSpan'] ?? 1)`: a null span is 1, not 0.
            cs = php_int_cast(_or_one(cell.get("colSpan") if isinstance(cell, dict) else None))
            rs = php_int_cast(_or_one(cell.get("rowSpan") if isinstance(cell, dict) else None))
            if r + rs > n:
                return {"ok": False, "width": width, "anchors": anchors}
            placed.append((r, c))
            for dr in range(rs):
                for dc in range(cs):
                    if dr == 0:
                        occ.add(c + dc)
                    else:
                        covered.setdefault(r + dr, set()).add(c + dc)
            span += cs
            c += cs
        anchors.append(placed)
        top = max(occ) + 1 if occ else 0
        w = covered_count + span
        if w != top:
            return {"ok": False, "width": width, "anchors": anchors}
        if width == -1:
            width = w
        elif w != width:
            return {"ok": False, "width": width, "anchors": anchors}
    return {"ok": True, "width": max(width, 0), "anchors": anchors}


def _canonical(t: str, v: Any) -> str:
    """PHP `Display::canonical()`."""
    if t == "number":
        return _format_float(v if _is_number(v) else 0)
    if t == "bool":
        return "TRUE" if _php_truthy(v) else "FALSE"
    if t == "empty":
        return ""
    return v if isinstance(v, str) else _php_json_encode(v)


def _display(t: str, v: Any, fmt: Any) -> str:
    """PHP `Display::display()`: the English-only number display a spreadsheet shows."""
    kind = fmt.get("kind") if isinstance(fmt, dict) else None

    if t == "number" and kind in ("number", "currency", "percent") and _is_number(v):
        decimals = fmt.get("decimals")
        places = int(decimals) if _is_json_int(decimals) else _DEFAULT_DECIMALS[kind]
        negative = v < 0 or (isinstance(v, float) and v == 0.0 and math.copysign(1.0, v) < 0)
        d = _plain_decimal(v)
        if kind == "percent":
            d = _shift_two(d)
        rounded = _round_half_away(d, places)
        if _ZEROS.fullmatch(rounded):
            negative = False
        whole, dot, frac = rounded.partition(".")
        if kind != "percent":
            whole = _THOUSANDS.sub(",", whole)
        num = whole + (dot + frac if dot else "")
        sign = "-" if negative else ""
        if kind == "percent":
            return sign + num + "%"
        if kind == "currency":
            code = fmt.get("currency") if isinstance(fmt.get("currency"), str) else "USD"
            return sign + _SYMBOLS.get(code, code + " ") + num
        return sign + num

    if t in ("date", "datetime") and kind in ("date", "datetime") and isinstance(v, str):
        date = v[:10]
        if kind == "date":
            return date
        return date + " " + (v[11:19] if t == "datetime" else "00:00:00")

    return _canonical(t, v)


def _plain_decimal(v: int | float) -> str:
    """The shortest decimal string that round-trips to |v|, without an exponent."""
    if isinstance(v, int):
        return str(abs(v))
    s = repr(abs(v))
    m = _EXPONENT.fullmatch(s)
    if m is None:
        return s
    digits = m.group(1) + (m.group(2) or "")
    point = len(m.group(1)) + int(m.group(3))
    if point <= 0:
        return "0." + "0" * (-point) + digits
    if point >= len(digits):
        return digits + "0" * (point - len(digits))
    return digits[:point] + "." + digits[point:]


def _shift_two(d: str) -> str:
    """x100 by moving the decimal point two places to the right."""
    whole, _, frac = d.partition(".")
    f = frac.ljust(2, "0")
    shifted = (whole + f[:2]).lstrip("0") or "0"
    return shifted + ("." + f[2:] if len(f) > 2 else "")


def _round_half_away(d: str, places: int) -> str:
    """Round a plain non-negative decimal string to `places`, half away from zero, on its digits."""
    whole, _, frac = d.partition(".")
    if len(frac) <= places:
        return whole + ("." + frac.ljust(places, "0") if places > 0 else "")
    digits = list(whole + frac[:places])
    if int(frac[places]) >= 5:
        i = len(digits) - 1
        while i >= 0:
            if digits[i] == "9":
                digits[i] = "0"
                i -= 1
            else:
                digits[i] = str(int(digits[i]) + 1)
                break
        if i < 0:
            digits.insert(0, "1")
    joined = "".join(digits)
    w = joined[: len(joined) - places] or "0"
    return w + "." + joined[-places:] if places > 0 else w


def _format_float(v: int | float) -> str:
    """PHP `Display::formatFloat()`: number_format to 14 places, trimmed, `-0` as `0`."""
    try:
        value = float(v)
    except OverflowError:
        return "0"
    return format_float(value) if math.isfinite(value) else "0"


def _is_number(v: Any) -> bool:
    """PHP is_int || is_float. bool first: True is an int here and not in PHP."""
    return not isinstance(v, bool) and isinstance(v, (int, float))


def _is_json_int(v: Any) -> bool:
    """PHP `Json::isInt`: an int, or a finite float with no fraction."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v) and math.floor(v) == v


def _or_one(v: Any) -> Any:
    return 1 if v is None else v


def _gt_one(v: Any) -> bool:
    """PHP `$x > 1` for a span as JSON can carry it."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v > 1
    if isinstance(v, str):
        try:
            return float(v) > 1
        except ValueError:
            return False
    return v is not None and not isinstance(v, (list, dict))


def _php_string(v: Any) -> str:
    """PHP's `(string)` of the scalar `t`."""
    if isinstance(v, str):
        return v
    if v is None or v is False:
        return ""
    if v is True:
        return "1"
    if isinstance(v, (int, float)):
        return str(v) if isinstance(v, int) else format_float(v)
    return ""


def _php_truthy(v: Any) -> bool:
    """PHP truthiness: "", "0", 0, 0.0, None, False and empty arrays are false."""
    if v is None or v is False or v == "" or v == "0":
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _php_json_encode(v: Any) -> str:
    """PHP `json_encode()` with default flags for what a malformed cell can carry:
    `/` escaped, non-ASCII as `\\uXXXX` (as PHP writes both). Floats use
    Python's shortest repr, which matches PHP's except in exponent spelling."""
    return json.dumps(v, ensure_ascii=True, separators=(",", ":")).replace("/", "\\/")
