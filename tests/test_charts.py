"""Native charts. Mirrors the PHP engine's ChartsTest case for case.

A chart is a real Excel chart over CELL RANGES, not a picture: edit a figure and
the chart follows. Byte parity with PHP is the ``charts`` entry in
``tests/fixtures.py``; these cases also keep that fixture honest, since a part
diff of two engines that both wrote no chart parts would pass.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from holy_sheet import read, to_bytes, tool_definition, validate


def _workbook(charts: list[Any], **extra: Any) -> dict[str, Any]:
    return {
        "sheets": [
            {
                "name": "Q3 Revenue",
                "cells": {
                    "A4": "Region", "B4": "Revenue", "C4": "Growth",
                    "A5": "North", "B5": 1250000.5, "C5": 0.184,
                    "A6": "South", "B6": 980400.25, "C6": -0.042,
                    "A7": "EMEA", "B7": 2100000, "C7": 0.311,
                },
                "charts": charts,
                **extra,
            }
        ]
    }


def _parts(schema: dict[str, Any]) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(to_bytes(schema))) as z:
        return {name: z.read(name).decode("utf-8") for name in z.namelist()}


REVENUE = {
    "type": "column",
    "title": "Q3 revenue by region",
    "categories": "A5:A7",
    "series": [{"name": "Revenue", "values": "B5:B7"}],
    "position": {"from": "E2", "to": "L18"},
}


def test_writes_a_chart_over_live_cell_ranges_wired_into_the_sheet() -> None:
    parts = _parts(_workbook([REVENUE]))
    assert '<drawing r:id="rId1"/>' in parts["xl/worksheets/sheet1.xml"]
    assert 'Target="../drawings/drawing1.xml"' in parts["xl/worksheets/_rels/sheet1.xml.rels"]
    assert 'Target="../charts/chart1.xml"' in parts["xl/drawings/_rels/drawing1.xml.rels"]
    assert 'PartName="/xl/drawings/drawing1.xml"' in parts["[Content_Types].xml"]
    assert 'PartName="/xl/charts/chart1.xml"' in parts["[Content_Types].xml"]

    chart = parts["xl/charts/chart1.xml"]
    assert '<c:barChart><c:barDir val="col"/>' in chart
    assert "<c:f>'Q3 Revenue'!$B$5:$B$7</c:f>" in chart
    assert "<c:f>'Q3 Revenue'!$A$5:$A$7</c:f>" in chart
    assert '<c:pt idx="2"><c:v>2100000</c:v></c:pt>' in chart
    assert '<c:pt idx="0"><c:v>North</c:v></c:pt>' in chart
    assert "Q3 revenue by region" in chart
    assert "<c:legend>" not in chart


def test_anchors_the_chart_between_the_two_cells_it_is_given() -> None:
    drawing = _parts(_workbook([REVENUE]))["xl/drawings/drawing1.xml"]
    assert "<xdr:from><xdr:col>4</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>" in drawing
    assert "<xdr:to><xdr:col>11</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>17</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>" in drawing


def test_draws_data_labels_a_legend_for_several_series_and_a_value_format() -> None:
    chart = _parts(_workbook([{
        **REVENUE,
        "series": [{"name": "Revenue", "values": "B5:B7", "color": "#F97316"}, {"name": "Growth", "values": "C5:C7"}],
        "dataLabels": True,
        "format": "$#,##0",
    }]))["xl/charts/chart1.xml"]
    assert '<c:showVal val="1"/>' in chart
    assert "<c:legend>" in chart
    assert '<a:srgbClr val="F97316"/>' in chart
    assert 'formatCode="$#,##0"' in chart


def test_makes_line_and_pie_charts_and_a_horizontal_bar() -> None:
    line = _parts(_workbook([{**REVENUE, "type": "line"}]))["xl/charts/chart1.xml"]
    pie = _parts(_workbook([{**REVENUE, "type": "pie"}]))["xl/charts/chart1.xml"]
    bar = _parts(_workbook([{**REVENUE, "type": "bar"}]))["xl/charts/chart1.xml"]
    assert "<c:lineChart>" in line
    assert "<c:pieChart>" in pie
    assert "<c:valAx>" not in pie
    assert '<c:barDir val="bar"/>' in bar


def test_numbers_charts_across_sheets_and_keeps_comment_rels_first() -> None:
    schema = _workbook([REVENUE, {**REVENUE, "type": "line", "position": {"from": "E20", "to": "L36"}}])
    schema["sheets"][0]["cells"]["A1"] = {"value": "Title", "comment": {"text": "note"}}
    schema["sheets"].append({"name": "Second", "cells": {"A1": 1, "A2": 2}, "charts": [{"type": "column", "series": [{"values": "A1:A2"}]}]})
    parts = _parts(schema)

    for key in ("xl/charts/chart1.xml", "xl/charts/chart2.xml", "xl/charts/chart3.xml", "xl/drawings/drawing1.xml", "xl/drawings/drawing2.xml"):
        assert key in parts
    assert (
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>'
        in parts["xl/worksheets/_rels/sheet1.xml.rels"]
    )
    assert '<drawing r:id="rId3"/><legacyDrawing r:id="rId1"/>' in parts["xl/worksheets/sheet1.xml"]
    assert 'Target="../charts/chart3.xml"' in parts["xl/drawings/_rels/drawing2.xml.rels"]


def test_adds_nothing_when_there_are_no_charts() -> None:
    parts = _parts(_workbook([]))
    assert [p for p in parts if "chart" in p or "drawing" in p] == []
    assert "<drawing" not in parts["xl/worksheets/sheet1.xml"]


def test_reads_a_chart_back_as_it_was_written() -> None:
    chart = {
        "type": "column",
        "title": "Q3 revenue by region",
        "categories": "A5:A7",
        "series": [{"name": "Revenue", "values": "B5:B7", "color": "#F97316"}, {"name": "Growth", "values": "C5:C7"}],
        "position": {"from": "E2", "to": "L18"},
        "dataLabels": True,
        "legend": True,
        "format": "$#,##0",
    }
    got = read(to_bytes(_workbook([chart])))["sheets"][0]["charts"]
    # json.dumps pins key ORDER as well, which PHP's toBe does.
    assert json.dumps(got) == json.dumps([chart])


def test_refuses_a_malformed_chart_with_a_hint() -> None:
    paths = [e["path"] for e in validate(_workbook([
        {"type": "donut", "series": [{"values": "B5:B7"}]},
        {"type": "column", "series": []},
        {"type": "column", "series": [{"values": "B5-B7"}], "categories": "nope", "position": {"from": "L18", "to": "E2"}},
    ]))]
    assert "sheets[0].charts[0].type" in paths
    assert "sheets[0].charts[1].series" in paths
    assert "sheets[0].charts[2].series[0].values" in paths
    assert "sheets[0].charts[2].categories" in paths
    assert "sheets[0].charts[2].position" in paths


def test_publishes_charts_in_the_schema() -> None:
    sheet = tool_definition()["definitions"]["Sheet"]["properties"]
    assert "charts" in sheet
    assert "categories" in json.dumps(sheet["charts"])
