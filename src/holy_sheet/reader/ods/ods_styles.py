"""Cell styles and data styles -> `CellFormat`.

Mirrors PHP `Reader\\Ods\\OdsStyles`, where what is and is not mapped is
documented. A cell's style is found in content.xml's automatic styles, then
styles.xml's common styles, and inherits along `style:parent-style-name` down to
the table-cell `style:default-style`. The nearest style that sets a property
wins.
"""

from __future__ import annotations

import math
import re
from typing import Any
from xml.etree.ElementTree import Element

from ...workbook.cell_format import CellFormat
from ..format.num_fmt_parser import SYMBOL_TO_ISO
from . import ns

_PT_SIZE = re.compile(r"([0-9]*\.?[0-9]+)pt", re.ASCII)
_BORDER_OFF = re.compile(r"\b(none|hidden)\b", re.ASCII)
_BORDER_HEX = re.compile(r"#([0-9a-f]{6})\b", re.ASCII)
_HEX = re.compile(r"#([0-9a-fA-F]{6})", re.ASCII)
_ISO_CODE = re.compile(r"[A-Z]{3}", re.ASCII)
_PCRE_SPACE = re.compile(r"[ \t\n\r\f\v]+")


class OdsStyles:
    def __init__(self, content: Element | None, styles: Element | None) -> None:
        self._automatic: dict[str, Element] = {}
        self._common: dict[str, Element] = {}
        self._default: Element | None = None
        self._data_styles: dict[str, Element] = {}
        self._resolved: dict[str, dict[str, Any]] = {}

        if content is not None:
            self._index(ns.child(content, ns.OFFICE, "automatic-styles"), self._automatic)
        if styles is not None:
            self._index(ns.child(styles, ns.OFFICE, "styles"), self._common)
            self._index(ns.child(styles, ns.OFFICE, "automatic-styles"), self._common)
        self._default_font_size = self._properties("Default")["font_size"]

    def cell_format(
        self,
        style_name: str,
        value_type: str | None,
        currency: str | None,
        date_has_time: bool,
    ) -> CellFormat | None:
        """The format of one cell.

        `style_name` is the cell's effective style (cell, row default, column
        default, or "Default"); `value_type` is office:value-type; `currency` is
        the office:currency ISO code; `date_has_time` says whether a date value
        carries a time of day.
        """
        p = self._properties(style_name)
        data: dict[str, Any] | None = p["data"]
        display = data.get("displayFormat") if data else None

        # The value type says what the value IS; the data style only says how it is shown.
        if value_type == "date":
            data = {"displayFormat": display if display in ("date", "datetime") else ("datetime" if date_has_time else "date")}
        elif value_type == "time":
            data = {"displayFormat": "datetime"}
        elif value_type == "percentage":
            if display != "percentage":
                data = {"displayFormat": "percentage", **({"decimals": data["decimals"]} if data and data.get("decimals") is not None else {})}
        elif value_type == "currency":
            if display != "currency":
                data = {"displayFormat": "currency", **({"decimals": data["decimals"]} if data and data.get("decimals") is not None else {})}
            else:
                data = dict(data)  # type: ignore[arg-type]
            if currency is not None and currency != "":
                data["currency"] = currency

        fmt = CellFormat(
            bold=p["bold"],
            italic=p["italic"],
            text_align=p["text_align"],
            display_format=data.get("displayFormat") if data else None,
            decimals=data.get("decimals") if data else None,
            color=p["color"],
            background_color=p["background_color"],
            font_size=p["font_size"] if p["font_size"] is not None and p["font_size"] != self._default_font_size else None,
            border_top=p["border_top"],
            border_right=p["border_right"],
            border_bottom=p["border_bottom"],
            border_left=p["border_left"],
            currency=data.get("currency") if data else None,
        )
        return None if fmt.is_empty() else fmt

    # ------------------------------------------------------------------ #

    def _index(self, container: Element | None, into: dict[str, Element]) -> None:
        if container is None:
            return
        for el in ns.children_in(container, ns.STYLE):
            family = ns.attr(el, ns.STYLE, "family") or ""
            name = ns.local(el)
            if name == "default-style" and family == "table-cell":
                if self._default is None:
                    self._default = el
            elif name == "style" and family == "table-cell":
                into.setdefault(ns.attr(el, ns.STYLE, "name") or "", el)
        for el in ns.children_in(container, ns.NUMBER):
            self._data_styles.setdefault(ns.attr(el, ns.STYLE, "name") or "", el)

    def _chain(self, name: str) -> list[Element]:
        """The style and its ancestors, nearest first, ending with the default style."""
        chain: list[Element] = []
        seen: set[str] = set()
        # The cell's own style is normally automatic; a parent is always common.
        # Explicit None checks: an Element with no children is falsy.
        el = self._automatic.get(name)
        if el is None:
            el = self._common.get(name)
        while el is not None and name not in seen:
            seen.add(name)
            chain.append(el)
            name = ns.attr(el, ns.STYLE, "parent-style-name") or ""
            el = None if name == "" else self._common.get(name)
            if el is None and name != "":
                el = self._automatic.get(name)
        if self._default is not None:
            chain.append(self._default)
        return chain

    def _properties(self, name: str) -> dict[str, Any]:
        cached = self._resolved.get(name)
        if cached is not None:
            return cached

        chain = self._chain(name)
        weight = _first(chain, "text-properties", ns.FO, "font-weight")
        style = _first(chain, "text-properties", ns.FO, "font-style")
        size = _first(chain, "text-properties", ns.FO, "font-size")

        data_style: str | None = None
        for el in chain:
            candidate = ns.attr(el, ns.STYLE, "data-style-name") or ""
            if candidate != "":
                data_style = candidate
                break

        size_match = _PT_SIZE.fullmatch(size) if size is not None else None
        resolved = {
            "bold": weight is not None and (weight == "bold" or (ns.php_is_numeric(weight) and ns.php_int(weight) >= 600)),
            "italic": style in ("italic", "oblique"),
            "text_align": _text_align(chain),
            "color": _color(chain),
            "background_color": _background(chain),
            "font_size": math.trunc(float(size_match.group(1))) if size_match else None,
            "border_top": _border(chain, "top"),
            "border_right": _border(chain, "right"),
            "border_bottom": _border(chain, "bottom"),
            "border_left": _border(chain, "left"),
            "data": self._data_format(data_style, 0),
        }
        self._resolved[name] = resolved
        return resolved

    def _data_format(self, name: str | None, depth: int) -> dict[str, Any] | None:
        """A data style -> displayFormat, decimals, currency."""
        if name is None or depth > 4:
            return None
        el = self._data_styles.get(name)
        if el is None:
            return None

        # LibreOffice writes a signed format as the NEGATIVE sub-format with a map
        # to the positive one; the positive one is the format as authored.
        for mapping in ns.children_in(el, ns.STYLE):
            if ns.local(mapping) != "map":
                continue
            condition = _PCRE_SPACE.sub("", ns.attr(mapping, ns.STYLE, "condition") or "")
            if condition in ("value()>=0", "value()>0"):
                mapped = self._data_format(ns.attr(mapping, ns.STYLE, "apply-style-name") or "", depth + 1)
                if mapped is not None:
                    return mapped

        parts = ns.children_in(el, ns.NUMBER)
        names = [ns.local(p) for p in parts]
        number = next((p for p in parts if ns.local(p) == "number"), None)
        decimals: int | None = None
        if number is not None:
            places = ns.attr(number, ns.NUMBER, "decimal-places")
            if places is None:
                places = ns.attr(number, ns.NUMBER, "min-decimal-places")
            decimals = ns.php_int(places) if places is not None else None

        kind = ns.local(el)
        if kind == "number-style":
            if "scientific-number" in names or "fraction" in names:
                return None
            # A currency written as literal text around a number: "$#,##0.00"
            # converted from xlsx arrives this way, not as a currency-style.
            for text in (p for p in parts if ns.local(p) == "text"):
                raw = ns.own_text(text)
                for ch in ("-", "(", ")", '"', "\u00a0"):
                    raw = raw.replace(ch, "")
                iso = _currency_code(raw)
                if iso is not None:
                    return {"displayFormat": "currency", "decimals": decimals if decimals is not None else 0, "currency": iso}
            if number is None:
                return None
            # A number with no decimal places set is "General".
            return {"displayFormat": "auto"} if decimals is None else {"displayFormat": "number", "decimals": decimals}
        if kind == "percentage-style":
            return {"displayFormat": "percentage", "decimals": decimals if decimals is not None else 0}
        if kind == "currency-style":
            out: dict[str, Any] = {"displayFormat": "currency", "decimals": decimals if decimals is not None else 0}
            symbol = next((p for p in parts if ns.local(p) == "currency-symbol"), None)
            iso = _currency_code(ns.own_text(symbol)) if symbol is not None else None
            if iso is not None:
                out["currency"] = iso
            return out
        if kind == "date-style":
            if any(n in names for n in ("hours", "minutes", "seconds", "am-pm")):
                return {"displayFormat": "datetime"}
            return {"displayFormat": "date"}
        if kind == "time-style":
            return {"displayFormat": "datetime"}
        if kind == "text-style":
            return {"displayFormat": "text"}
        return None  # boolean-style, and anything newer than this reader


def _first(chain: list[Element], properties: str, namespace: str, name: str) -> str | None:
    """The nearest value of one property attribute."""
    for el in chain:
        value = ns.attr(ns.child(el, ns.STYLE, properties), namespace, name)
        if value is not None:
            return ns.php_trim(value)
    return None


def _color(chain: list[Element]) -> str | None:
    for el in chain:
        props = ns.child(el, ns.STYLE, "text-properties")
        if props is None:
            continue
        # "Automatic" colour: whatever the viewer's window text is. No fixed colour.
        if ns.attr(props, ns.STYLE, "use-window-font-color") == "true":
            return None
        value = ns.attr(props, ns.FO, "color")
        if value is not None:
            return _hex(value)
    return None


def _background(chain: list[Element]) -> str | None:
    value = _first(chain, "table-cell-properties", ns.FO, "background-color")
    return None if value is None or value == "transparent" else _hex(value)


def _border(chain: list[Element], side: str) -> str | None:
    for el in chain:
        props = ns.child(el, ns.STYLE, "table-cell-properties")
        if props is None:
            continue
        raw = ns.attr(props, ns.FO, f"border-{side}")
        if raw is None:
            raw = ns.attr(props, ns.FO, "border")
        if raw is None:
            continue

        value = ns.ascii_lower(ns.php_trim(raw))
        if value == "" or _BORDER_OFF.search(value):
            return None
        match = _BORDER_HEX.search(value)
        return "#" + match.group(1).upper() if match else "#000000"
    return None


def _text_align(chain: list[Element]) -> str | None:
    # text-align-source="value-type" means "align by what the value is": the
    # stored fo:text-align is not in effect.
    if _first(chain, "table-cell-properties", ns.STYLE, "text-align-source") == "value-type":
        return None
    align = _first(chain, "paragraph-properties", ns.FO, "text-align")
    if align is None or align == "":
        return None
    if align == "start":
        return "left"
    if align == "end":
        return "right"
    return align


def _hex(value: str) -> str | None:
    match = _HEX.fullmatch(ns.php_trim(value))
    return "#" + match.group(1).upper() if match else None


def _currency_code(symbol: str) -> str | None:
    s = ns.php_trim(symbol)
    if s == "":
        return None
    if _ISO_CODE.fullmatch(s):
        return s
    return SYMBOL_TO_ISO.get(s)
