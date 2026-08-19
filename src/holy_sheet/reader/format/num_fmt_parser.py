"""The reverse of `NumFmtBuilder`.

Maps an Excel number-format code (or a built-in numFmtId) back to a Holy Sheet
`displayFormat` + `decimals` + `currency` triple. Designed to recognise both
halves of the problem: every code this package's writer emits (the common path),
and the 50 standard built-in ids so a foreign Excel file still describes
cleanly.

Returns None when nothing matches -- the caller keeps the raw code rather than
guessing.
"""

from __future__ import annotations

import re
from typing import Any

_BUILTIN = {
    0: "General",
    1: "0",
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    9: "0%",
    10: "0.00%",
    11: "0.00E+00",
    12: "# ?/?",
    13: "# ??/??",
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
    37: "#,##0 ;(#,##0)",
    38: "#,##0 ;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)",
    40: "#,##0.00;[Red](#,##0.00)",
    45: "mm:ss",
    46: "[h]:mm:ss",
    47: "mmss.0",
    48: "##0.0E+0",
    49: "@",
}

# Symbol -> ISO, mirroring NumFmtBuilder's table. The yen sign is ambiguous
# (the writer emits it for both JPY and CNY); JPY is the more common read.
_SYMBOL_TO_ISO = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
}

_QUOTED_PREFIX = re.compile(r'^"([^"]+)"')
_LITERALS = re.compile(r'"[^"]*"|\[[^\]]*\]')
_DATE_TOKEN = re.compile(r"[ymd]", re.IGNORECASE)
_TIME_TOKEN = re.compile(r"[hHsS]")
_PLAIN_NUMBER = re.compile(r"^#?,?#?#?0(\.0+)?$")
_DECIMALS = re.compile(r"0\.(0+)")


class NumFmtParser:
    @staticmethod
    def parse(format_code: str | None) -> dict[str, Any] | None:
        if format_code is None:
            return None
        code = format_code

        if code in ("", "General", "@"):
            return {"displayFormat": "text" if code == "@" else "auto"}

        if _looks_like_date(code):
            return {"displayFormat": "datetime" if _has_time_component(code) else "date"}

        if "%" in code:
            return {"displayFormat": "percentage", "decimals": _decimals_after(code)}

        match = _QUOTED_PREFIX.match(code)
        if match:
            iso = _SYMBOL_TO_ISO.get(match.group(1))
            result: dict[str, Any] = {
                "displayFormat": "currency",
                "decimals": _decimals_after(code),
            }
            if iso is not None:
                result["currency"] = iso
            return result

        if _PLAIN_NUMBER.match(code.split(";")[0]):
            return {"displayFormat": "number", "decimals": _decimals_after(code)}

        return None

    @staticmethod
    def parse_builtin(num_fmt_id: int) -> dict[str, Any] | None:
        code = _BUILTIN.get(num_fmt_id)
        if code is None:
            return None
        return NumFmtParser.parse(code)


def _looks_like_date(code: str) -> bool:
    # Quoted literals and [locale] prefixes first -- "Monday" is not a date
    # token and `[$-409]` is not a month.
    return _DATE_TOKEN.search(_LITERALS.sub("", code)) is not None


def _has_time_component(code: str) -> bool:
    stripped = _LITERALS.sub("", code)
    # `mm:` is minutes, not months, whenever a colon follows it.
    return _TIME_TOKEN.search(stripped) is not None or "mm:" in stripped.lower()


def _decimals_after(code: str) -> int:
    match = _DECIMALS.search(code.split(";")[0])
    return len(match.group(1)) if match else 0
