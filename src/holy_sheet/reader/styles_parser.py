"""`xl/styles.xml` -> a list of `CellFormat` indexed by xf id.

Excel's style model is indirect: a cell carries `s="N"`, which indexes
`cellXfs`, whose Nth `<xf>` points at a font, a fill, a border and a number
format. This flattens all of that into one lookup the worksheet parser can use
directly.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element

from ..workbook.cell_format import CellFormat
from .format.num_fmt_parser import NumFmtParser
from .xml import attr, find, find_all, parse_xml_or_none


class StylesParser:
    @staticmethod
    def parse(styles_xml: bytes | str | None) -> list[CellFormat | None]:
        root = parse_xml_or_none(styles_xml)
        if root is None:
            return []

        num_fmts = _parse_num_fmts(root)
        fonts = _parse_fonts(root)
        fills = _parse_fills(root)
        borders = _parse_borders(root)

        xfs: list[CellFormat | None] = []
        for xf in find_all(find(root, "cellXfs"), "xf"):
            font_id = _int_attr(xf, "fontId", 0)
            fill_id = _int_attr(xf, "fillId", 0)
            border_id = _int_attr(xf, "borderId", 0)
            num_fmt_id = _int_attr(xf, "numFmtId", 0)

            alignment = find(xf, "alignment")
            align = attr(alignment, "horizontal") if alignment is not None else None

            font = fonts[font_id] if font_id < len(fonts) else None
            fill = fills[fill_id] if fill_id < len(fills) else None
            border = borders[border_id] if border_id < len(borders) else None
            custom_code = num_fmts.get(num_fmt_id)
            num_fmt = (
                NumFmtParser.parse(custom_code)
                if custom_code is not None
                else NumFmtParser.parse_builtin(num_fmt_id)
            )

            # Index 0 is Excel's unformatted base; keeping it as None (rather
            # than an empty CellFormat) is what makes an unstyled cell describe
            # back with no `format` key at all.
            if not xfs and font is None and fill is None and border is None and not num_fmt and align is None:
                xfs.append(None)
                continue

            xfs.append(_build_cell_format(font, fill, border, num_fmt, align))

        return xfs


def _parse_num_fmts(root: Element) -> dict[int, str]:
    out: dict[int, str] = {}
    for num_fmt in find_all(find(root, "numFmts"), "numFmt"):
        num_fmt_id = attr(num_fmt, "numFmtId")
        if num_fmt_id is None:
            continue
        out[int(num_fmt_id)] = attr(num_fmt, "formatCode") or ""
    return out


def _parse_fonts(root: Element) -> list[dict[str, Any]]:
    fonts: list[dict[str, Any]] = []
    for font in find_all(find(root, "fonts"), "font"):
        size_el = find(font, "sz")
        colour_el = find(font, "color")
        rgb = attr(colour_el, "rgb") if colour_el is not None else None
        fonts.append(
            {
                "bold": find(font, "b") is not None,
                "italic": find(font, "i") is not None,
                "size": int(float(attr(size_el, "val") or 11)) if size_el is not None else 11,
                "color": _argb_to_hex(rgb) if rgb else None,
            }
        )
    return fonts


def _parse_fills(root: Element) -> list[str | None]:
    fills: list[str | None] = []
    for fill in find_all(find(root, "fills"), "fill"):
        pattern_fill = find(fill, "patternFill")
        pattern = attr(pattern_fill, "patternType") if pattern_fill is not None else "none"
        if pattern != "solid":
            fills.append(None)
            continue
        fg = find(pattern_fill, "fgColor")
        rgb = attr(fg, "rgb") if fg is not None else None
        fills.append(_argb_to_hex(rgb) if rgb else None)
    return fills


def _parse_borders(root: Element) -> list[dict[str, str | None]]:
    borders: list[dict[str, str | None]] = []
    for border in find_all(find(root, "borders"), "border"):
        record: dict[str, str | None] = {"top": None, "right": None, "bottom": None, "left": None}
        for side in ("top", "right", "bottom", "left"):
            edge = find(border, side)
            if edge is None or not attr(edge, "style"):
                continue
            colour = find(edge, "color")
            rgb = attr(colour, "rgb") if colour is not None else None
            if rgb:
                record[side] = _argb_to_hex(rgb)
        borders.append(record)
    return borders


def _build_cell_format(
    font: dict[str, Any] | None,
    fill: str | None,
    border: dict[str, str | None] | None,
    num_fmt: dict[str, Any] | None,
    align: str | None,
) -> CellFormat:
    font = font or {}
    border = border or {}
    num_fmt = num_fmt or {}
    size = font.get("size")
    return CellFormat(
        bold=bool(font.get("bold", False)),
        italic=bool(font.get("italic", False)),
        text_align=align,
        display_format=num_fmt.get("displayFormat"),
        decimals=num_fmt.get("decimals"),
        color=font.get("color"),
        background_color=fill,
        # 11pt is Calibri's default, so recording it would put a `fontSize` key
        # on every cell in every described workbook.
        font_size=size if size is not None and size != 11 else None,
        border_top=border.get("top"),
        border_right=border.get("right"),
        border_bottom=border.get("bottom"),
        border_left=border.get("left"),
        currency=num_fmt.get("currency"),
    )


def _int_attr(element: Element, name: str, default: int) -> int:
    value = attr(element, name)
    return int(value) if value is not None else default


def _argb_to_hex(argb: str) -> str:
    text = argb.upper()
    if len(text) == 8:
        return "#" + text[2:]
    if len(text) == 6:
        return "#" + text
    return "#000000"
