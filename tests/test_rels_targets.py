"""Relationship targets resolve per OPC (ECMA-376 Part 2).

A target resolves against the directory of the part that owns the
relationship, unless it starts with "/", which is package-absolute. openpyxl
writes absolute targets (`/xl/worksheets/sheet1.xml`); this reader used to turn
that into `xl/xl/worksheets/sheet1.xml`, find nothing and silently return zero
sheets. External targets are never parts, and a relationship whose part is
missing raises `MissingPartException` instead of being skipped.

Driven by the PHP repo's tests/fixtures/rels-targets/cases.json (packages
assembled by hand from raw XML, expectations written by hand), the same bytes
all three ports pass. The tests at the bottom zip their own raw XML, so this
port's resolution is pinned even without that checkout's newest fixtures.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from holy_sheet import MissingPartException, read
from holy_sheet.reader.rels_parser import RelsParser
from tests import _oracle


def _dir() -> Path:
    src = _oracle.php_src_root()
    path = src.parent / "tests" / "fixtures" / "rels-targets" if src is not None else None
    if path is None or not (path / "cases.json").is_file():
        raise RuntimeError(
            "rels-targets fixtures not found. They live in the PHP holy-sheet repo at "
            "tests/fixtures/rels-targets; check it out beside this one or set HOLY_SHEET_PHP_SRC."
        )
    return path


DIR = _dir()
CASES: dict[str, Any] = json.loads((DIR / "cases.json").read_text(encoding="utf-8"))


def _fixture(name: str) -> bytes:
    return (DIR / name).read_bytes()


def _projection(schema: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sheet in schema["sheets"]:
        cells: dict[str, Any] = {}
        bold: list[str] = []
        comments: dict[str, str] = {}
        for address, cell in sheet.get("cells", {}).items():
            cells[address] = cell.get("value")
            if cell.get("format", {}).get("bold") is True:
                bold.append(address)
            if "comment" in cell:
                comments[address] = cell["comment"]["text"]
        out.append({
            "name": sheet["name"],
            "cells": cells,
            "bold": bold,
            "comments": comments,
            "charts": [c["type"] for c in sheet.get("charts", [])],
        })
    return out


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in cases]


def test_has_cases_to_run() -> None:
    assert len(CASES["read"]) > 10
    assert len(CASES["raises"]) > 8


@pytest.mark.parametrize("case", CASES["read"], ids=_ids(CASES["read"]))
def test_reads_each_shared_rels_target_fixture(case: dict[str, Any]) -> None:
    schema = read(_fixture(case["file"]), **case["options"])

    assert _projection(schema) == CASES["projections"][case["sheets"]]


@pytest.mark.parametrize("case", CASES["raises"], ids=_ids(CASES["raises"]))
def test_raises_for_each_shared_missing_part(case: dict[str, Any]) -> None:
    with pytest.raises(MissingPartException) as caught:
        read(_fixture(case["file"]), **case["options"])

    assert caught.value.part == case["part"]
    assert caught.value.source == case["source"]
    assert str(caught.value).startswith("[holy-sheet] ")
    if case["part"] is not None:
        assert case["part"] in str(caught.value)


def test_a_missing_part_is_still_a_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        read(_fixture("missing-sheet.xlsx"))


# --- this port's own raw-XML packages ---------------------------------------

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _rels(*rels: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{_REL}/{t}" Target="{target}"/>' for i, t, target in rels)
    return f'<Relationships xmlns="{_PKG}">{body}</Relationships>'


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buf.getvalue()


def _openpyxl_layout(sheet_target: str, sheet_part: str) -> dict[str, str]:
    return {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        "_rels/.rels": _rels(("rId1", "officeDocument", "/xl/workbook.xml")),
        "xl/workbook.xml": f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets><sheet name="Only" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": _rels(("rId1", "worksheet", sheet_target)),
        sheet_part: f'<worksheet xmlns="{_MAIN}"><sheetData><row r="1"><c r="A1"><v>42</v></c></row></sheetData></worksheet>',
    }


def test_reads_a_package_absolute_worksheet_target() -> None:
    data = _zip(_openpyxl_layout("/xl/worksheets/sheet1.xml", "xl/worksheets/sheet1.xml"))

    assert read(data)["sheets"][0]["cells"]["A1"]["value"] == 42


def test_an_absolute_target_to_nothing_raises_instead_of_reading_zero_sheets() -> None:
    parts = _openpyxl_layout("/xl/worksheets/sheet1.xml", "xl/worksheets/elsewhere.xml")

    with pytest.raises(MissingPartException) as caught:
        read(_zip(parts))
    assert caught.value.part == "xl/worksheets/sheet1.xml"


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ("xl/workbook.xml", "worksheets/sheet1.xml", "xl/worksheets/sheet1.xml"),
        ("xl/workbook.xml", "/xl/worksheets/sheet1.xml", "xl/worksheets/sheet1.xml"),
        ("xl/worksheets/sheet1.xml", "../comments1.xml", "xl/comments1.xml"),
        ("xl/worksheets/sheet1.xml", "/xl/comments/comment1.xml", "xl/comments/comment1.xml"),
        ("xl/worksheets/sheet1.xml", "./../drawings/./drawing1.xml", "xl/drawings/drawing1.xml"),
        # The package-level rels (_rels/.rels) belong to the package root, "".
        ("", "xl/workbook.xml", "xl/workbook.xml"),
        ("xl/workbook.xml", "../../../../x.xml", "x.xml"),
    ],
)
def test_resolves_a_target_against_its_source_part(source: str, target: str, expected: str) -> None:
    assert RelsParser.resolve(source, target) == expected


def test_parses_the_target_mode() -> None:
    rels = RelsParser.parse(
        f'<Relationships xmlns="{_PKG}">'
        f'<Relationship Id="rId1" Type="{_REL}/hyperlink" Target="https://example.com/" TargetMode="External"/>'
        f'<Relationship Id="rId2" Type="{_REL}/comments" Target="../comments1.xml"/></Relationships>'
    )

    assert RelsParser.is_external(rels["rId1"])
    assert not RelsParser.is_external(rels["rId2"])
