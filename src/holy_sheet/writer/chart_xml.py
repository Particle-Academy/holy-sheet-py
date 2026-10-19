"""The parts a native chart needs. Mirrors PHP ``Writer\\ChartXml`` byte for byte.

A drawing per sheet (where each chart sits, anchored between two cells), and a
chart part per chart (DrawingML over CELL RANGES, with the values cached so a
viewer that does not recalculate still draws them). String-built like the rest
of the writer: the bytes are the cross-engine contract.
"""

from __future__ import annotations

import math
from typing import Any

from ..helpers.php import format_float
from ..workbook.chart_range import ChartRange
from ..workbook.workbook import Workbook

_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def drawing(charts: list[dict[str, Any]], first_chart_num: int) -> str:
    """One sheet's drawing: an anchor per chart. Chart k on this sheet is rId{k+1}."""
    anchors = ""
    for k, chart in enumerate(charts):
        anchors += (
            '<xdr:twoCellAnchor editAs="oneCell">'
            + _marker("from", chart["from"])
            + _marker("to", chart["to"])
            + '<xdr:graphicFrame macro="">'
            + f'<xdr:nvGraphicFramePr><xdr:cNvPr id="{k + 2}" name="Chart {first_chart_num + k}"/><xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
            + '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
            + f'<a:graphic><a:graphicData uri="{_NS_C}">'
            + f'<c:chart xmlns:c="{_NS_C}" xmlns:r="{_NS_R}" r:id="rId{k + 1}"/>'
            + "</a:graphicData></a:graphic>"
            + "</xdr:graphicFrame>"
            + "<xdr:clientData/>"
            + "</xdr:twoCellAnchor>"
        )
    return (
        _XML_DECL
        + f'<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="{_NS_A}">'
        + anchors
        + "</xdr:wsDr>"
    )


def drawing_rels(charts: list[dict[str, Any]], first_chart_num: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{k + 1}" Type="{_NS_R}/chart" Target="../charts/chart{first_chart_num + k}.xml"/>'
        for k in range(len(charts))
    )
    return (
        _XML_DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + rels
        + "</Relationships>"
    )


def chart(chart: dict[str, Any], workbook: Workbook) -> str:
    chart_type = chart["type"]
    series = "".join(_series(i, s, chart, workbook) for i, s in enumerate(chart["series"]))

    labels = _data_labels(chart_type, chart["format"]) if chart["dataLabels"] else ""
    if chart_type == "line":
        plot = (
            '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
            + series + labels
            + '<c:marker val="1"/><c:axId val="10"/><c:axId val="20"/></c:lineChart>'
        )
    elif chart_type == "pie":
        plot = '<c:pieChart><c:varyColors val="1"/>' + series + labels + '<c:firstSliceAng val="0"/></c:pieChart>'
    else:
        plot = (
            f'<c:barChart><c:barDir val="{"bar" if chart_type == "bar" else "col"}"/><c:grouping val="clustered"/><c:varyColors val="0"/>'
            + series + labels
            + '<c:gapWidth val="80"/><c:axId val="10"/><c:axId val="20"/></c:barChart>'
        )
    # A bar starts at zero. Mirrors PHP ChartXml::chart.
    zero_base = chart_type != "line" and _non_negative(chart, workbook)
    axes = "" if chart_type == "pie" else _axes(chart_type == "bar", chart["format"], zero_base)

    legend = chart["legend"]
    if legend is None:
        legend = len(chart["series"]) > 1 or chart_type == "pie"
    if chart["title"] is not None:
        title = (
            '<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="1200" b="1"/></a:pPr>'
            + '<a:r><a:rPr lang="en-US" sz="1200" b="1"/><a:t>' + _escape(chart["title"]) + "</a:t></a:r></a:p></c:rich></c:tx>"
            + '<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
        )
    else:
        title = '<c:autoTitleDeleted val="1"/>'

    return (
        _XML_DECL
        + f'<c:chartSpace xmlns:c="{_NS_C}" xmlns:a="{_NS_A}" xmlns:r="{_NS_R}">'
        + '<c:roundedCorners val="0"/>'
        + "<c:chart>" + title
        + "<c:plotArea><c:layout/>" + plot + axes + "</c:plotArea>"
        + ('<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>' if legend else "")
        + '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        + "</c:chart>"
        + "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        + _text_props("595959", 900)
        + "</c:chartSpace>"
    )


def _marker(tag: str, at: list[int]) -> str:
    return (
        f"<xdr:{tag}><xdr:col>{at[0]}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{at[1]}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:{tag}>"
    )


def _series(i: int, s: dict[str, Any], chart: dict[str, Any], workbook: Workbook) -> str:
    chart_type = chart["type"]
    name = "<c:tx><c:v>" + _escape(s["name"]) + "</c:v></c:tx>" if s["name"] is not None else ""
    fill = ""
    if s["color"] is not None and chart_type != "pie":
        color = s["color"]
        fill = (
            f'<c:spPr><a:ln w="28575" cap="rnd"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:round/></a:ln></c:spPr>'
            if chart_type == "line"
            else f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></c:spPr>'
        )
    invert = '<c:invertIfNegative val="0"/>' if chart_type in ("column", "bar") else ""
    marker = '<c:marker><c:symbol val="circle"/><c:size val="6"/></c:marker>' if chart_type == "line" else ""
    cat = "<c:cat>" + _str_ref(chart["categories"], workbook) + "</c:cat>" if chart["categories"] is not None else ""
    smooth = '<c:smooth val="0"/>' if chart_type == "line" else ""
    # A coloured point (dPt) follows the series' own fill, invert and marker,
    # and precedes its categories. Mirrors PHP ChartXml::series.
    points = ""
    for p in s.get("points", []):
        solid = f'<a:solidFill><a:srgbClr val="{p["color"]}"/></a:solidFill>'
        if chart_type == "line":
            body = f'<c:marker><c:symbol val="circle"/><c:size val="6"/><c:spPr>{solid}<a:ln>{solid}</a:ln></c:spPr></c:marker><c:bubble3D val="0"/>'
        elif chart_type == "pie":
            body = f'<c:bubble3D val="0"/><c:spPr>{solid}</c:spPr>'
        else:
            body = f'<c:invertIfNegative val="0"/><c:bubble3D val="0"/><c:spPr>{solid}</c:spPr>'
        points += f'<c:dPt><c:idx val="{p["index"]}"/>' + body + "</c:dPt>"
    return (
        f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/>' + name + fill + invert + marker + points
        + cat + "<c:val>" + _num_ref(s["values"], workbook) + "</c:val>" + smooth + "</c:ser>"
    )


def _is_number(value: Any) -> bool:
    """PHP ``is_int || is_float``. ``bool`` is an int in Python and is NOT a number here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _str_ref(rng: ChartRange, workbook: Workbook) -> str:
    values = _values(rng, workbook)
    pts = "".join(
        f'<c:pt idx="{idx}"><c:v>' + _escape(_text(value)) + "</c:v></c:pt>"
        for idx, value in enumerate(values)
        if value is not None
    )
    return (
        "<c:strRef><c:f>" + _escape(rng.formula()) + "</c:f>"
        + f'<c:strCache><c:ptCount val="{len(values)}"/>' + pts + "</c:strCache></c:strRef>"
    )


def _num_ref(rng: ChartRange, workbook: Workbook) -> str:
    values = _values(rng, workbook)
    pts = "".join(
        f'<c:pt idx="{idx}"><c:v>' + _number(value) + "</c:v></c:pt>"
        for idx, value in enumerate(values)
        if _is_number(value)
    )
    return (
        "<c:numRef><c:f>" + _escape(rng.formula()) + "</c:f>"
        + f'<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="{len(values)}"/>' + pts
        + "</c:numCache></c:numRef>"
    )


def _values(rng: ChartRange, workbook: Workbook) -> list[Any]:
    """Each cell's cached value in range order: a formula cell's cached value, else its value."""
    cells: dict[str, Any] = {}
    for sheet in workbook.sheets:
        if sheet.name == rng.sheet:
            cells = sheet.cells
            break
    out: list[Any] = []
    for address in rng.addresses():
        cell = cells.get(address)
        out.append(None if cell is None else (cell.cached_value if cell.formula is not None else cell.value))
    return out


def _data_labels(chart_type: str, fmt: str | None) -> str:
    pos = "t" if chart_type == "line" else "bestFit" if chart_type == "pie" else "outEnd"
    return (
        "<c:dLbls>"
        + (f'<c:numFmt formatCode="{_attr(fmt)}" sourceLinked="0"/>' if fmt is not None else "")
        + "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        + _text_props("404040", 900)
        + f'<c:dLblPos val="{pos}"/><c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="0"/>'
        + '<c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/>'
        + "</c:dLbls>"
    )


def _non_negative(chart: dict[str, Any], workbook: Workbook) -> bool:
    # Every plotted (numeric) value is zero or more. Mirrors PHP ChartXml::nonNegative.
    return all(not (_is_number(v) and v < 0) for s in chart["series"] for v in _values(s["values"], workbook))


def _axes(horizontal: bool, fmt: str | None, zero_base: bool = False) -> str:
    cat_pos = "l" if horizontal else "b"
    val_pos = "b" if horizontal else "l"
    num_fmt = (
        f'<c:numFmt formatCode="{_attr(fmt)}" sourceLinked="0"/>'
        if fmt is not None
        else '<c:numFmt formatCode="General" sourceLinked="1"/>'
    )
    return (
        f'<c:catAx><c:axId val="10"/><c:scaling><c:orientation val="{"maxMin" if horizontal else "minMax"}"/></c:scaling>'
        + f'<c:delete val="0"/><c:axPos val="{cat_pos}"/><c:numFmt formatCode="General" sourceLinked="1"/>'
        # "low": category labels sit at the edge, clear of a negative bar.
        + '<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="low"/>'
        + '<c:spPr><a:ln w="9525"><a:solidFill><a:srgbClr val="BFBFBF"/></a:solidFill></a:ln></c:spPr>'
        + '<c:crossAx val="20"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/><c:noMultiLvlLbl val="0"/></c:catAx>'
        + '<c:valAx><c:axId val="20"/><c:scaling><c:orientation val="minMax"/>' + ('<c:min val="0"/>' if zero_base else "") + "</c:scaling>"
        + f'<c:delete val="0"/><c:axPos val="{val_pos}"/>'
        + '<c:majorGridlines><c:spPr><a:ln w="9525"><a:solidFill><a:srgbClr val="E5E7EB"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>'
        + num_fmt
        + '<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
        + "<c:spPr><a:ln><a:noFill/></a:ln></c:spPr>"
        + f'<c:crossAx val="10"/><c:crosses val="{"max" if horizontal else "autoZero"}"/><c:crossBetween val="between"/></c:valAx>'
    )


def _text_props(color: str, size: int) -> str:
    return (
        f'<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="{size}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:defRPr></a:pPr>'
        '<a:endParaRPr lang="en-US"/></a:p></c:txPr>'
    )


def _text(value: Any) -> str:
    """A category as text: numbers as the writer prints them, booleans as TRUE / FALSE."""
    if isinstance(value, bool):  # before int, always
        return "TRUE" if value else "FALSE"
    if _is_number(value):
        return _number(value)
    return str(value)


def _number(value: int | float) -> str:
    """A number as the cells print it: the shortest exact decimal, never an exponent."""
    if isinstance(value, int):
        # Past zend_long PHP's json_decode would have produced a float.
        if _INT64_MIN <= value <= _INT64_MAX:
            return str(value)
        value = float(value)
    if not math.isfinite(value):
        return "0"
    return format_float(value)


def _escape(text: str) -> str:
    """Element text: & < > only, as Excel writes it (a formula's 'Sheet' stays literal)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(text: str) -> str:
    """An attribute value: also both quotes."""
    return _escape(text).replace('"', "&quot;").replace("'", "&apos;")
