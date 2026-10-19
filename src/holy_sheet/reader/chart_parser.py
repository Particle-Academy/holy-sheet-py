"""Charts back into the normalised shape the writer takes. Mirrors PHP ``Reader\\ChartParser``.

A sheet's drawing (where each chart sits) plus each chart part (what it plots).
Reads the four types the writer draws; a chart of another type, or one that
plots no range, is skipped rather than guessed at. Namespace-aware, like PHP's
DOM lookups, because a chart from another producer can use other prefixes.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

from ..workbook.chart_range import ChartRange
from .xml import parse_xml_or_none

_NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# PHP ctype_digit: ASCII only. str.isdigit() would accept other scripts' digits.
_DIGITS = re.compile(r"^[0-9]+$")
_HEX6 = re.compile(r"^[0-9A-F]{6}$")
# PHP trim()'s default set.
_PHP_TRIM = " \t\n\r\0\x0b"


def _q(ns: str, name: str) -> str:
    return "{" + ns + "}" + name


def _load(xml: bytes | str) -> ElementTree.Element | None:
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    if "<!doctype" in text.lower():
        return None
    return parse_xml_or_none(xml)


def _descendants(root: ElementTree.Element, ns: str, name: str) -> list[ElementTree.Element]:
    """PHP getElementsByTagNameNS: descendants (not self), in document order."""
    tag = _q(ns, name)
    return [el for el in root.iter(tag) if el is not root]


def _all(root: ElementTree.Element, ns: str, name: str) -> list[ElementTree.Element]:
    """Same, on the DOCUMENT -- which includes the root element itself."""
    return list(root.iter(_q(ns, name)))


def _children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    tag = _q(_NS_C, name)
    return [child for child in parent if child.tag == tag]


def _child(parent: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    if parent is None:
        return None
    found = _children(parent, name)
    return found[0] if found else None


def _val(parent: ElementTree.Element, name: str) -> str | None:
    el = _child(parent, name)
    return None if el is None else el.get("val", "")


def _text(el: ElementTree.Element) -> str:
    return "".join(el.itertext())


def anchors(drawing_xml: bytes | str) -> list[dict[str, Any]]:
    """The anchors in a drawing: [{from: [col,row], to: [col,row], rel: rId}]."""
    root = _load(drawing_xml)
    if root is None:
        return []
    out: list[dict[str, Any]] = []
    for anchor in _all(root, _NS_XDR, "twoCellAnchor"):
        start = _marker(anchor, "from")
        end = _marker(anchor, "to")
        charts = _descendants(anchor, _NS_C, "chart")
        rel = charts[0].get(_q(_NS_R, "id"), "") if charts else ""
        if start is not None and end is not None and rel != "":
            out.append({"from": start, "to": end, "rel": rel})
    return out


def chart(chart_xml: bytes | str, sheet_name: str, start: list[int], end: list[int]) -> dict[str, Any] | None:
    """One chart part, normalised as normalize_charts would leave it, or None."""
    root = _load(chart_xml)
    if root is None:
        return None
    plot = None
    chart_type = None
    for tag in ("barChart", "lineChart", "pieChart"):
        found = _all(root, _NS_C, tag)
        if found:
            plot = found[0]
            if tag == "lineChart":
                chart_type = "line"
            elif tag == "pieChart":
                chart_type = "pie"
            else:
                chart_type = "bar" if _val(plot, "barDir") == "bar" else "column"
            break
    if plot is None or chart_type is None:
        return None

    categories = None
    series: list[dict[str, Any]] = []
    for ser in _children(plot, "ser"):
        val_ref = _formula(ser, "val")
        values = ChartRange.parse(val_ref, sheet_name) if val_ref is not None else None
        if values is None:
            continue
        if categories is None:
            cat_ref = _formula(ser, "cat")
            categories = ChartRange.parse(cat_ref, sheet_name) if cat_ref is not None else None
        series.append({"name": _series_name(ser), "values": values, "color": _series_color(ser)})
    if not series:
        return None

    labels = _child(plot, "dLbls")
    data_labels = labels is not None and _val(labels, "showVal") == "1"
    fmt = None
    val_axes = _all(root, _NS_C, "valAx")
    for host in (labels, val_axes[0] if val_axes else None):
        num_fmt = _child(host, "numFmt") if host is not None else None
        if num_fmt is not None and num_fmt.get("sourceLinked", "") == "0" and num_fmt.get("formatCode", "") != "":
            fmt = num_fmt.get("formatCode")
            break

    title = None
    titles = _all(root, _NS_C, "title")
    if titles:
        text = "".join(_text(t) for t in _descendants(titles[0], _NS_A, "t"))
        title = text if text != "" else None

    return {
        "type": chart_type,
        "title": title,
        "categories": categories,
        "series": series,
        "from": start,
        "to": end,
        "legend": True if _all(root, _NS_C, "legend") else None,
        "dataLabels": data_labels,
        "format": fmt,
    }


def _marker(anchor: ElementTree.Element, tag: str) -> list[int] | None:
    for node in anchor:
        if node.tag == _q(_NS_XDR, tag):
            cols = _descendants(node, _NS_XDR, "col")
            rows = _descendants(node, _NS_XDR, "row")
            if cols and rows:
                col = _text(cols[0]).strip(_PHP_TRIM)
                row = _text(rows[0]).strip(_PHP_TRIM)
                if _DIGITS.match(col) and _DIGITS.match(row):
                    return [int(col), int(row)]
    return None


def _formula(ser: ElementTree.Element, name: str) -> str | None:
    """The c:f under c:val / c:cat, through numRef or strRef."""
    host = _child(ser, name)
    if host is None:
        return None
    for ref in ("numRef", "strRef"):
        f = _child(_child(host, ref), "f")
        if f is not None and _text(f).strip(_PHP_TRIM) != "":
            return _text(f).strip(_PHP_TRIM)
    return None


def _series_name(ser: ElementTree.Element) -> str | None:
    tx = _child(ser, "tx")
    if tx is None:
        return None
    v = _child(tx, "v")
    if v is not None:
        return _text(v)
    cached = _descendants(tx, _NS_C, "v")
    return _text(cached[0]) if cached else None


def _series_color(ser: ElementTree.Element) -> str | None:
    sp_pr = _child(ser, "spPr")
    if sp_pr is None:
        return None
    colors = _descendants(sp_pr, _NS_A, "srgbClr")
    val = colors[0].get("val", "").upper() if colors else ""
    return val if _HEX6.match(val) else None
