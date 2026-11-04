"""Every XML part of an uploaded file is read through one gate, which refuses a
DOCTYPE. Mirrors PHP tests/Unit/DoctypeRefusalTest.php.

Everything goes through the public reader, because what matters is what a hostile
upload gets back, not what a helper returns. MARKER is the visible text of each
part in a clean file and ENTITY the value its replacement declares, so ENTITY in
the result means it expanded.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree
from xml.parsers import expat

import pytest

import holy_sheet
from holy_sheet.exceptions import OfficeTemplateRefused, UnsupportedFormatException
from holy_sheet.reader.xml import XmlError, XmlRefused, parse_xml, parse_xml_or_none, prolog_declares_doctype

MARKER = "HSBASE"
ENTITY = "HSINT"
DOCTYPE = f'<!DOCTYPE r [<!ENTITY x "{ENTITY}">]>'.encode()
MACRO_TEMPLATE = b"application/vnd.ms-excel.template.macroEnabled.main+xml"
SHEET_MAIN = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def zip_of(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return out.getvalue()


def xlsx_parts() -> dict[str, bytes]:
    """A real package from the real writer, plus shared strings it does not author."""
    data = holy_sheet.to_bytes(
        {
            "meta": {"creator": MARKER, "created": "2026-01-01T00:00:00Z"},
            "sheets": [
                {
                    "name": "Sheet1",
                    "cells": {
                        "A1": {"value": MARKER, "comment": {"text": MARKER, "author": "Auth"}},
                        "A2": {"value": 1, "format": {"displayFormat": "percentage"}},
                        "A3": 2,
                    },
                    "charts": [{"type": "column", "title": MARKER, "series": [{"values": "A2:A3"}]}],
                }
            ],
        }
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["xl/sharedStrings.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>'
        + MARKER.encode()
        + b"</t></si></sst>"
    )
    parts["xl/worksheets/sheet1.xml"] = re.sub(
        rb'<c r="A3"([^>]*)><v>2</v></c>', rb'<c r="A3"\1 t="s"><v>0</v></c>', parts["xl/worksheets/sheet1.xml"]
    )
    return parts


def with_doctype(xml: bytes, doctype: bytes = DOCTYPE) -> bytes:
    match = re.match(rb"^<\?xml[^>]*\?>\s*", xml)
    if match:
        return match.group(0) + doctype + xml[match.end() :]
    return doctype + xml


def declared(xml: bytes, encoding: str) -> str:
    return re.sub(rb"^<\?xml[^>]*\?>", f'<?xml version="1.0" encoding="{encoding}"?>'.encode(), xml).decode("utf-8")


ENCODINGS: dict[str, Callable[[bytes], bytes]] = {
    "plain UTF-8": lambda x: x,
    "UTF-8 with a BOM": lambda x: b"\xef\xbb\xbf" + x,
    "UTF-16LE with a BOM": lambda x: b"\xff\xfe" + declared(x, "UTF-16").encode("utf-16-le"),
    "UTF-16BE with a BOM": lambda x: b"\xfe\xff" + declared(x, "UTF-16").encode("utf-16-be"),
    "UTF-16LE without a BOM": lambda x: declared(x, "UTF-16").encode("utf-16-le"),
    "past a long comment": lambda x: re.sub(rb"^(<\?xml[^>]*\?>\s*)", rb"\1<!-- " + b"p" * 5000 + b" -->", x, count=1),
}

#: The parts this port reads whose marker reaches the result.
MARKER_PARTS = [
    "xl/worksheets/sheet1.xml",
    "xl/sharedStrings.xml",
    "docProps/core.xml",
    "xl/comments1.xml",
    "xl/charts/chart1.xml",
]


def referencing(parts: dict[str, bytes], part: str) -> bytes:
    return parts[part].replace(MARKER.encode(), b"&x;")


def read_text(parts: dict[str, bytes]) -> str:
    try:
        return json.dumps(holy_sheet.read(zip_of(parts)))
    except RuntimeError:
        return "(refused)"


def test_reads_every_marker_from_a_clean_file() -> None:
    # A1 value, comment, A3 shared string, chart title and creator: one from each
    # part this file proves. Styles has its own test: this port returns no format
    # code as text.
    assert read_text(xlsx_parts()).count(MARKER) == 5


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_does_not_take_a_number_format_from_an_entity(encoding: str) -> None:
    parts = xlsx_parts()
    assert holy_sheet.read(zip_of(parts))["sheets"][0]["cells"]["A2"]["format"]["displayFormat"] == "percentage"
    assert b'formatCode="0.0%"' in parts["xl/styles.xml"]
    parts["xl/styles.xml"] = ENCODINGS[encoding](
        with_doctype(
            parts["xl/styles.xml"].replace(b'formatCode="0.0%"', b'formatCode="&x;"'),
            b'<!DOCTYPE r [<!ENTITY x "0.0%">]>',
        )
    )
    assert "percentage" not in read_text(parts)


def test_the_fixture_entity_is_live_so_its_absence_is_the_gate() -> None:
    assert ElementTree.fromstring(b'<?xml version="1.0"?>' + DOCTYPE + b"<r>&x;</r>").text == ENTITY


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_the_fixture_entity_is_live_in_each_encoding(encoding: str) -> None:
    raw = ENCODINGS[encoding](with_doctype(b'<?xml version="1.0" encoding="UTF-8"?><r>&x;</r>'))
    assert ElementTree.fromstring(raw).text == ENTITY


@pytest.mark.parametrize("encoding", list(ENCODINGS))
@pytest.mark.parametrize("part", MARKER_PARTS)
def test_does_not_expand_an_internal_entity_from_any_part(part: str, encoding: str) -> None:
    parts = xlsx_parts()
    parts[part] = ENCODINGS[encoding](with_doctype(referencing(parts, part)))
    assert ENTITY not in read_text(parts)


@pytest.mark.parametrize("encoding", list(ENCODINGS))
@pytest.mark.parametrize("part", MARKER_PARTS + ["xl/workbook.xml"])
def test_still_reads_each_encoding_without_a_doctype(part: str, encoding: str) -> None:
    parts = xlsx_parts()
    clean = read_text(parts)
    parts[part] = ENCODINGS[encoding](parts[part])
    assert read_text(parts) == clean


@pytest.mark.parametrize(
    ("part", "target", "probe"),
    [
        ("xl/_rels/workbook.xml.rels", b"worksheets/sheet1.xml", '"Sheet1"'),
        ("xl/worksheets/_rels/sheet1.xml.rels", b"../comments1.xml", '"comment"'),
        ("xl/drawings/_rels/drawing1.xml.rels", b"../charts/chart1.xml", '"charts"'),
    ],
)
def test_does_not_follow_a_relationship_whose_target_comes_from_an_entity(part: str, target: bytes, probe: str) -> None:
    parts = xlsx_parts()
    assert target in parts[part]
    parts[part] = with_doctype(parts[part].replace(target, b"&x;"), b'<!DOCTYPE r [<!ENTITY x "' + target + b'">]>')
    assert probe not in read_text(parts)


def test_does_not_read_a_drawing_that_takes_an_anchor_from_an_entity() -> None:
    parts = xlsx_parts()
    drawing = parts["xl/drawings/drawing1.xml"]
    assert b"<xdr:col>2</xdr:col>" in drawing
    parts["xl/drawings/drawing1.xml"] = with_doctype(
        drawing.replace(b"<xdr:col>2</xdr:col>", b"<xdr:col>&x;</xdr:col>"), b'<!DOCTYPE r [<!ENTITY x "2">]>'
    )
    assert '"charts"' not in read_text(parts)


# ─── What a consumer sees, per part: the same table as PHP ────────────────────


def test_throws_for_a_refused_workbook_part() -> None:
    parts = xlsx_parts()
    parts["xl/workbook.xml"] = with_doctype(parts["xl/workbook.xml"])
    with pytest.raises(RuntimeError, match="failed to parse xl/workbook.xml"):
        holy_sheet.read(zip_of(parts))


def test_keeps_the_sheet_with_no_cells_for_a_refused_worksheet_part() -> None:
    parts = xlsx_parts()
    parts["xl/worksheets/sheet1.xml"] = with_doctype(parts["xl/worksheets/sheet1.xml"])
    sheet = holy_sheet.read(zip_of(parts))["sheets"][0]
    assert sheet["name"] == "Sheet1"
    assert not sheet.get("cells")


def test_reads_the_other_parts_without_refused_shared_strings() -> None:
    parts = xlsx_parts()
    parts["xl/sharedStrings.xml"] = with_doctype(parts["xl/sharedStrings.xml"])
    cells = holy_sheet.read(zip_of(parts))["sheets"][0]["cells"]
    assert cells["A1"]["value"] == MARKER
    assert cells["A3"].get("value", "") != MARKER


# ─── The package declaration ──────────────────────────────────────────────────


def macro_template() -> dict[str, bytes]:
    parts = xlsx_parts()
    assert SHEET_MAIN in parts["[Content_Types].xml"]
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(SHEET_MAIN, MACRO_TEMPLATE)
    return parts


def test_refuses_a_macro_template_for_its_declaration() -> None:
    with pytest.raises(OfficeTemplateRefused, match="macro_enabled"):
        holy_sheet.read(zip_of(macro_template()))


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_reads_a_clean_declaration_in_every_encoding(encoding: str) -> None:
    parts = xlsx_parts()
    clean = read_text(parts)
    parts["[Content_Types].xml"] = ENCODINGS[encoding](parts["[Content_Types].xml"])
    assert read_text(parts) == clean


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_still_refuses_the_macro_template_when_its_declaration_carries_a_doctype(encoding: str) -> None:
    parts = macro_template()
    parts["[Content_Types].xml"] = ENCODINGS[encoding](with_doctype(parts["[Content_Types].xml"]))
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(zip_of(parts))


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_refuses_a_plain_workbook_whose_declaration_carries_a_doctype(encoding: str) -> None:
    parts = xlsx_parts()
    parts["[Content_Types].xml"] = ENCODINGS[encoding](with_doctype(parts["[Content_Types].xml"]))
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(zip_of(parts))


def test_refuses_a_declaration_the_parser_cannot_read_and_so_cannot_vouch_for() -> None:
    parts = xlsx_parts()
    parts["[Content_Types].xml"] = (
        b'<?xml version="1.0" encoding="UTF-7"?>'
        b"+ADw-+ACE-DOCTYPE r +AFs-+ADw-+ACE-ENTITY x +ACI-HSINT+ACI-+AD4-+AF0-+AD4-"
        b"+ADw-Types/+AD4-"
    )
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(zip_of(parts))


def test_refuses_a_declaration_with_a_truncated_doctype() -> None:
    parts = xlsx_parts()
    parts["[Content_Types].xml"] = b'<?xml version="1.0"?><!DOCTYPE r'
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(zip_of(parts))


def test_convert_template_refuses_a_declaration_with_a_doctype() -> None:
    parts = xlsx_parts()
    parts["[Content_Types].xml"] = with_doctype(parts["[Content_Types].xml"])
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.convert_template(zip_of(parts))


def test_leaves_a_malformed_declaration_handled_as_it_always_was() -> None:
    parts = xlsx_parts()
    clean = read_text(parts)
    parts["[Content_Types].xml"] = b"<Types>"
    assert read_text(parts) == clean


def test_does_not_refuse_a_declaration_for_text_that_only_looks_like_a_doctype() -> None:
    parts = xlsx_parts()
    clean = read_text(parts)
    parts["[Content_Types].xml"] = with_doctype(parts["[Content_Types].xml"], b"<!-- <!DOCTYPE x> -->").replace(
        b"</Types>", b"<!-- <!DOCTYPE x> --></Types>"
    )
    assert read_text(parts) == clean


# ─── External entities ────────────────────────────────────────────────────────


@pytest.fixture
def canary(tmp_path: Path) -> str:
    target = tmp_path / "canary.txt"
    target.write_text("HSCANARY", encoding="ascii")
    return target.resolve().as_uri()


def test_a_permissive_parser_can_reach_the_canary_so_no_leak_below_means_something(canary: str) -> None:
    loads: list[str] = []
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_ALWAYS)

    def load(context: str, base: str | None, system: str, public: str | None) -> int:
        loads.append(system)
        return 1

    parser.ExternalEntityRefHandler = load
    parser.Parse(f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "{canary}">]><r>&x;</r>', True)
    assert loads == [canary]


@pytest.mark.parametrize("part", MARKER_PARTS)
def test_does_not_leak_a_file_entity(part: str, canary: str) -> None:
    parts = xlsx_parts()
    parts[part] = with_doctype(referencing(parts, part), f'<!DOCTYPE r [<!ENTITY x SYSTEM "{canary}">]>'.encode())
    assert "HSCANARY" not in read_text(parts)


# ─── Text that only looks like a DOCTYPE ──────────────────────────────────────


def test_keeps_a_shared_string_written_as_cdata() -> None:
    parts = xlsx_parts()
    parts["xl/sharedStrings.xml"] = parts["xl/sharedStrings.xml"].replace(
        b"<t>HSBASE</t>", b"<t><![CDATA[<!DOCTYPE html>]]></t>"
    )
    assert holy_sheet.read(zip_of(parts))["sheets"][0]["cells"]["A3"]["value"] == "<!DOCTYPE html>"


def test_keeps_a_chart_title_written_as_cdata() -> None:
    parts = xlsx_parts()
    parts["xl/charts/chart1.xml"] = parts["xl/charts/chart1.xml"].replace(b"HSBASE", b"<![CDATA[<!DOCTYPE html>]]>")
    assert holy_sheet.read(zip_of(parts))["sheets"][0]["charts"][0]["title"] == "<!DOCTYPE html>"


def test_does_not_stop_a_part_being_read_from_a_comment() -> None:
    parts = xlsx_parts()
    parts["xl/worksheets/sheet1.xml"] = with_doctype(parts["xl/worksheets/sheet1.xml"], b"<!-- <!DOCTYPE x> -->")
    assert holy_sheet.read(zip_of(parts))["sheets"][0]["cells"]["A1"]["value"] == MARKER


# ─── ods ──────────────────────────────────────────────────────────────────────


def ods_parts(text: bytes = MARKER.encode()) -> dict[str, bytes]:
    ns = (
        b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        b'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        b'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    )
    decl = b'<?xml version="1.0" encoding="UTF-8"?>'
    return {
        "mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
        "content.xml": decl
        + b"<office:document-content "
        + ns
        + b'><office:body><office:spreadsheet><table:table table:name="S"><table:table-row>'
        + b'<table:table-cell office:value-type="string"><text:p>'
        + text
        + b"</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet></office:body>"
        + b"</office:document-content>",
        "styles.xml": decl + b"<office:document-styles " + ns + b"/>",
        "meta.xml": decl
        + b"<office:document-meta "
        + ns
        + b"><office:meta><meta:initial-creator>"
        + text
        + b"</meta:initial-creator></office:meta></office:document-meta>",
    }


def test_reads_a_clean_ods() -> None:
    assert read_text(ods_parts()).count(MARKER) == 2


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_throws_for_a_refused_ods_content_part(encoding: str) -> None:
    parts = ods_parts(b"&x;")
    parts["content.xml"] = ENCODINGS[encoding](with_doctype(parts["content.xml"]))
    with pytest.raises(RuntimeError, match="failed to parse content.xml"):
        holy_sheet.read(zip_of(parts))


@pytest.mark.parametrize("encoding", list(ENCODINGS))
def test_does_not_expand_from_ods_meta(encoding: str) -> None:
    parts = ods_parts()
    parts["meta.xml"] = ENCODINGS[encoding](with_doctype(parts["meta.xml"].replace(MARKER.encode(), b"&x;")))
    assert ENTITY not in read_text(parts)


# ─── The gate itself ──────────────────────────────────────────────────────────


def _prolog_id(xml: bytes) -> str:
    return f"{len(xml)}-{xml[:12].hex()}"


@pytest.mark.parametrize(
    "xml",
    [
        DOCTYPE + b"<r/>",
        b'<?xml version="1.0"?>' + DOCTYPE + b"<r/>",
        b'<?xml version="1.0"?>\n <!-- c -->\n' + DOCTYPE + b"<r/>",
        b"\xef\xbb\xbf" + DOCTYPE + b"<r/>",
        b"<!-- " + b"p" * 100000 + b" -->" + DOCTYPE + b"<r/>",
        b"<?pi a?>" + DOCTYPE + b"<r/>",
        b"\xff\xfe" + ('<?xml version="1.0" encoding="UTF-16"?>' + DOCTYPE.decode() + "<r/>").encode("utf-16-le"),
        b"\xfe\xff" + ('<?xml version="1.0" encoding="UTF-16"?>' + DOCTYPE.decode() + "<r/>").encode("utf-16-be"),
        ('<?xml version="1.0" encoding="UTF-16"?>' + DOCTYPE.decode() + "<r/>").encode("utf-16-le"),
        ('<?xml version="1.0" encoding="UTF-16"?>' + DOCTYPE.decode() + "<r/>").encode("utf-16-be"),
    ],
    ids=_prolog_id,
)
def test_the_prolog_check_finds_a_doctype_before_the_root(xml: bytes) -> None:
    assert prolog_declares_doctype(xml)


@pytest.mark.parametrize(
    "xml",
    [
        b"",
        b"<r/>",
        b"<r><![CDATA[<!DOCTYPE html>]]></r>",
        b"<!-- <!DOCTYPE x> --><r/>",
        b"<?pi <!DOCTYPE x>?><r/>",
        b"<!-- " + DOCTYPE,
        b"<?xml " + DOCTYPE,
    ],
)
def test_the_prolog_check_finds_none_where_there_is_none(xml: bytes) -> None:
    assert not prolog_declares_doctype(xml)


def test_the_parser_refuses_a_doctype_the_prolog_check_cannot_read() -> None:
    # A comment in UTF-16 can hold a code unit that reads as the end of a comment
    # once NULs are dropped, so the byte walk stops early. The parser does not.
    misleading = '<!-- ⴭ> x --><!DOCTYPE r [<!ENTITY x "HSINT">]><r>&x;</r>'
    raw = b"\xff\xfe" + misleading.encode("utf-16-le")
    assert ElementTree.fromstring(raw).text == ENTITY
    assert not prolog_declares_doctype(raw)
    with pytest.raises(XmlRefused, match="DOCTYPE"):
        parse_xml(raw)


def test_a_clean_part_returns_what_the_primitive_returns() -> None:
    root = parse_xml(b'<?xml version="1.0"?><r a="1"><b>t</b></r>')
    assert root.attrib == {"a": "1"}
    assert root[0].text == "t"


def test_malformed_is_an_error_but_not_a_refusal() -> None:
    with pytest.raises(XmlError) as caught:
        parse_xml(b"<r>")
    assert not isinstance(caught.value, XmlRefused)
    assert parse_xml_or_none(b"<r>") is None


@pytest.mark.parametrize("encoding", ["UTF-7", "Shift_JIS", "zlib", "undefined", "no-such-encoding"])
def test_an_unreadable_encoding_is_refused_rather_than_escaping_the_gate(encoding: str) -> None:
    raw = f'<?xml version="1.0" encoding="{encoding}"?><r/>'.encode()
    with pytest.raises(XmlError):
        parse_xml(raw)
    assert parse_xml_or_none(raw) is None


def test_a_sheet_in_an_unreadable_encoding_reads_as_absent_instead_of_raising() -> None:
    parts = xlsx_parts()
    parts["xl/worksheets/sheet1.xml"] = b'<?xml version="1.0" encoding="Shift_JIS"?><worksheet/>'
    assert holy_sheet.read(zip_of(parts))["sheets"][0]["name"] == "Sheet1"


def test_leaves_no_way_to_a_part_except_through_the_gate() -> None:
    root = Path(holy_sheet.__file__).resolve().parent
    pattern = re.compile(r"ElementTree\.(fromstring|parse|iterparse|XML|XMLParser|XMLPullParser)\b|\bET\.|xml\.parsers|xml\.sax|minidom|pulldom|\blxml\b")
    offenders = [
        path.name
        for path in root.rglob("*.py")
        if path.name != "xml.py" or path.parent.name != "reader"
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    # And the walk really does read the source: the gate itself matches.
    assert pattern.search((root / "reader" / "xml.py").read_text(encoding="utf-8"))
