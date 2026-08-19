"""Dedup for Excel's four style sub-records, plus the cellXfs that combine them.

Excel's style model is awkward: every distinct combination of font + fill +
border + numFmt + alignment is one `<xf>` record, and cells reference it by
index (`<c s="3">`). Without dedup, styles.xml grows linearly with row count.

Style index 0 is deliberately the "no formatting" xf, which is what Excel
expects of index 0.
"""

from __future__ import annotations

from typing import Any

from ..helpers.xml import xml_escape_raw
from ..workbook.cell_format import CellFormat
from .format.num_fmt_builder import NumFmtBuilder


class StylesRegistry:
    def __init__(self) -> None:
        self._xf_index: dict[Any, int] = {"__default__": 0}
        self._xfs: list[dict[str, Any]] = [
            {"fontId": 0, "fillId": 0, "borderId": 0, "numFmtId": 0, "align": None}
        ]

        self._font_index: dict[Any, int] = {"__default__": 0}
        self._fonts: list[dict[str, Any]] = [
            {"size": 11, "name": "Calibri", "color": None, "bold": False, "italic": False}
        ]

        self._fill_index: dict[Any, int] = {"__none__": 0, "__gray125__": 1}
        self._fills: list[dict[str, Any]] = [{"type": "none"}, {"type": "gray125"}]

        self._border_index: dict[Any, int] = {"__default__": 0}
        self._borders: list[dict[str, Any]] = [
            {"top": None, "right": None, "bottom": None, "left": None}
        ]

        self._num_fmt_index: dict[str, int] = {}
        self._num_fmts: dict[int, str] = {}
        self._next_num_fmt_id = 164

    def register(self, fmt: CellFormat | None) -> int:
        """Register a format and return the xf index Excel will reference."""
        if fmt is None or fmt.is_empty():
            return 0

        key = fmt.key()
        if key in self._xf_index:
            return self._xf_index[key]

        record = {
            "fontId": self._font_for(fmt),
            "fillId": self._fill_for(fmt),
            "borderId": self._border_for(fmt),
            "numFmtId": self._num_fmt_for(fmt),
            "align": fmt.text_align,
        }
        self._xfs.append(record)
        index = len(self._xfs) - 1
        self._xf_index[key] = index
        return index

    def _font_for(self, fmt: CellFormat) -> int:
        record = {
            "size": 11 if fmt.font_size is None else fmt.font_size,
            "name": "Calibri",
            "color": fmt.color,
            "bold": fmt.bold,
            "italic": fmt.italic,
        }
        key = ("font", record["size"], record["color"], record["bold"], record["italic"])
        if key in self._font_index:
            return self._font_index[key]
        self._fonts.append(record)
        index = len(self._fonts) - 1
        self._font_index[key] = index
        return index

    def _fill_for(self, fmt: CellFormat) -> int:
        if fmt.background_color is None:
            return 0
        key = fmt.background_color.upper()
        if key in self._fill_index:
            return self._fill_index[key]
        self._fills.append({"type": "solid", "fg": key})
        index = len(self._fills) - 1
        self._fill_index[key] = index
        return index

    def _border_for(self, fmt: CellFormat) -> int:
        if not (fmt.border_top or fmt.border_right or fmt.border_bottom or fmt.border_left):
            return 0
        record = {
            "top": fmt.border_top,
            "right": fmt.border_right,
            "bottom": fmt.border_bottom,
            "left": fmt.border_left,
        }
        key = ("border", record["top"], record["right"], record["bottom"], record["left"])
        if key in self._border_index:
            return self._border_index[key]
        self._borders.append(record)
        index = len(self._borders) - 1
        self._border_index[key] = index
        return index

    def _num_fmt_for(self, fmt: CellFormat) -> int:
        code = NumFmtBuilder.build(fmt)
        if code is None:
            return 0
        if code in self._num_fmt_index:
            return self._num_fmt_index[code]
        num_fmt_id = self._next_num_fmt_id
        self._next_num_fmt_id += 1
        self._num_fmts[num_fmt_id] = code
        self._num_fmt_index[code] = num_fmt_id
        return num_fmt_id

    def to_xml(self) -> str:
        parts: list[str] = []

        if self._num_fmts:
            records = "".join(
                f'<numFmt numFmtId="{num_fmt_id}" formatCode="{xml_escape_raw(code)}"/>'
                for num_fmt_id, code in self._num_fmts.items()
            )
            parts.append(f'<numFmts count="{len(self._num_fmts)}">{records}</numFmts>')

        fonts = [f'<fonts count="{len(self._fonts)}">']
        for font in self._fonts:
            fonts.append("<font>")
            # PHP writes `(float) $size`, so 11 becomes "11" only because PHP
            # renders 11.0 as "11". Python's str(11.0) is "11.0", so the int
            # form is used deliberately -- these sizes are whole numbers by
            # construction (CellFormat.font_size is an int).
            fonts.append(f'<sz val="{_php_float_attr(font["size"])}"/>')
            if font["bold"]:
                fonts.append("<b/>")
            if font["italic"]:
                fonts.append("<i/>")
            if font["color"]:
                fonts.append(f'<color rgb="{_hex_to_argb(font["color"])}"/>')
            fonts.append(f'<name val="{xml_escape_raw(str(font["name"]))}"/>')
            fonts.append("</font>")
        fonts.append("</fonts>")
        parts.append("".join(fonts))

        fills = [f'<fills count="{len(self._fills)}">']
        for fill in self._fills:
            kind = fill.get("type", "")
            if kind == "none":
                fills.append('<fill><patternFill patternType="none"/></fill>')
            elif kind == "gray125":
                fills.append('<fill><patternFill patternType="gray125"/></fill>')
            else:
                fills.append(
                    '<fill><patternFill patternType="solid"><fgColor rgb="'
                    + _hex_to_argb(fill["fg"])
                    + '"/></patternFill></fill>'
                )
        fills.append("</fills>")
        parts.append("".join(fills))

        borders = [f'<borders count="{len(self._borders)}">']
        for border in self._borders:
            borders.append("<border>")
            for side in ("left", "right", "top", "bottom"):
                colour = border.get(side)
                if not colour:
                    borders.append(f"<{side}/>")
                else:
                    borders.append(
                        f'<{side} style="thin"><color rgb="{_hex_to_argb(colour)}"/></{side}>'
                    )
            borders.append("<diagonal/></border>")
        borders.append("</borders>")
        parts.append("".join(borders))

        cell_xfs = [f'<cellXfs count="{len(self._xfs)}">']
        for xf in self._xfs:
            apply = ""
            if xf["fontId"] > 0:
                apply += ' applyFont="1"'
            if xf["fillId"] > 0:
                apply += ' applyFill="1"'
            if xf["borderId"] > 0:
                apply += ' applyBorder="1"'
            if xf["numFmtId"] > 0:
                apply += ' applyNumberFormat="1"'
            if xf["align"]:
                apply += ' applyAlignment="1"'
            cell_xfs.append(
                f'<xf numFmtId="{xf["numFmtId"]}" fontId="{xf["fontId"]}"'
                f' fillId="{xf["fillId"]}" borderId="{xf["borderId"]}" xfId="0"{apply}>'
            )
            if xf["align"]:
                cell_xfs.append(
                    f'<alignment horizontal="{xml_escape_raw(str(xf["align"]))}"/>'
                )
            # Never self-closed, even when empty -- `<xf .../>` is a different
            # byte sequence and the parity suite diffs bytes.
            cell_xfs.append("</xf>")
        cell_xfs.append("</cellXfs>")

        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(parts)
            + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            + "".join(cell_xfs)
            + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
            "</styleSheet>"
        )


def _php_float_attr(value: Any) -> str:
    """PHP `(float) $v` interpolated into a string: 11 -> "11", 11.5 -> "11.5"."""
    number = float(value)
    return str(int(number)) if number == int(number) else repr(number)


def _hex_to_argb(hex_colour: str) -> str:
    """"#RRGGBB" -> "FFRRGGBB" (Excel stores ARGB)."""
    text = hex_colour.lstrip("#")
    if len(text) == 6:
        return "FF" + text.upper()
    if len(text) == 8:
        return text.upper()
    return "FF000000"
