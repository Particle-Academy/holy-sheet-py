"""Official template formats: Excel .xltx and LibreOffice .ots.

Mirrors PHP tests/Unit/OfficeTemplateTest.php and the Node port's
tests/office-template.test.ts.

A template IS its document twin with a different declared type: the main part's
content type in [Content_Types].xml (OOXML), or the `mimetype` entry plus the
manifest's root entry (ODF 1.3 Part 2 section 3.3 requires the two to be
equal). read() marks `sourceKind: "template"`; the converter flips the
declaration and copies every other part byte for byte.

Fixtures are built here with the standard library's `zipfile`, independently of
the production archive code, so a converter bug cannot also forge its evidence.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

import holy_sheet
from holy_sheet import cli
from holy_sheet.exceptions import ArchiveRefused, OfficeTemplateRefused, UnsupportedFormatException
from holy_sheet.reader.xlsx_reader import XlsxReader

DOC = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
TEMPLATE = "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml"
MACRO_TEMPLATE = "application/vnd.ms-excel.template.macroEnabled.main+xml"
MACRO_DOC = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
PPT_TEMPLATE = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
OTS = "application/vnd.oasis.opendocument.spreadsheet-template"

SCHEMA = {
    "sheets": [
        {
            "name": "Budget",
            "columns": [{"header": "Item"}, {"header": "Cost"}],
            "rows": [["Rent", 1200], ["Power", 85]],
        }
    ]
}


def entries(data: bytes) -> dict[str, bytes]:
    """Entry name -> uncompressed bytes, in archive order (stdlib zipfile)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def methods(data: bytes) -> dict[str, int]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {info.filename: info.compress_type for info in archive.infolist()}


def zip_of(files: dict[str, bytes]) -> bytes:
    """Re-zip in order; `mimetype` STORED (as ODF requires), the rest deflated."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return out.getvalue()


def xlsx() -> bytes:
    return holy_sheet.to_bytes(SCHEMA)


def retyped(content_type: str, extra: dict[str, bytes] | None = None, base: bytes | None = None) -> bytes:
    """The xlsx with its main part re-declared; nothing else differs."""
    files = entries(base if base is not None else xlsx())
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(DOC.encode(), content_type.encode())
    files.update(extra or {})
    return zip_of(files)


def xltx() -> bytes:
    return retyped(TEMPLATE)


def odf(mimetype: str, body: str = "spreadsheet", manifest_type: str | None = None) -> bytes:
    manifest_type = manifest_type or mimetype
    inner = (
        '<table:table table:name="Budget"><table:table-row>'
        '<table:table-cell office:value-type="string"><text:p>Rent</text:p></table:table-cell>'
        '<table:table-cell office:value-type="float" office:value="1200"><text:p>1200</text:p></table:table-cell>'
        "</table:table-row></table:table>"
        if body == "spreadsheet"
        else "<text:p>Not a spreadsheet</text:p>"
    )
    return zip_of(
        {
            "mimetype": mimetype.encode(),
            "content.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
                ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
                ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" office:version="1.3">'
                f"<office:body><office:{body}>{inner}</office:{body}></office:body></office:document-content>"
            ).encode(),
            "Thumbnails/opaque.bin": bytes([0, 1, 2, 98, 255]),
            "META-INF/manifest.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">\n'
                f' <manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="{manifest_type}"/>\n'
                ' <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>\n'
                "</manifest:manifest>"
            ).encode(),
        }
    )


def refused(reason: str, action) -> None:
    with pytest.raises(OfficeTemplateRefused) as caught:
        action()
    assert caught.value.reason == reason


def archive_refused(reason: str, action) -> None:
    with pytest.raises(ArchiveRefused) as caught:
        action()
    assert caught.value.reason == reason


def without(read: dict) -> dict:
    return {key: value for key, value in read.items() if key != "sourceKind"}


# ─── Reading ──────────────────────────────────────────────────────────────


def test_reads_an_excel_xltx_and_marks_it_as_a_template() -> None:
    base = xlsx()
    template = holy_sheet.read(retyped(TEMPLATE, base=base))
    assert template.get("sourceKind") == "template"
    assert without(template) == holy_sheet.read(base)


def test_leaves_an_xlsx_read_exactly_as_it_was() -> None:
    assert "sourceKind" not in holy_sheet.read(xlsx())


def test_reads_a_libreoffice_ots_and_marks_it_as_a_template() -> None:
    document = holy_sheet.read(odf(ODS))
    template = holy_sheet.read(odf(OTS))
    assert "sourceKind" not in document
    assert template.get("sourceKind") == "template"
    assert without(template) == document


def test_marks_the_workbook_itself_for_read_workbook_callers() -> None:
    assert XlsxReader().read_workbook(xltx()).source_kind == "template"
    assert XlsxReader().read_workbook(xlsx()).source_kind == "document"


def test_keeps_a_template_read_valid_and_writable() -> None:
    template = holy_sheet.read(xltx())
    assert holy_sheet.validate(template) == []
    assert "sourceKind" not in holy_sheet.read(holy_sheet.to_bytes(template))


def test_refuses_a_macro_enabled_xltm() -> None:
    refused("macro_enabled", lambda: holy_sheet.read(retyped(MACRO_TEMPLATE)))


def test_refuses_an_xltx_that_carries_a_vba_project_anyway() -> None:
    refused("macro_enabled", lambda: holy_sheet.read(retyped(TEMPLATE, {"xl/vbaProject.bin": b"not really"})))


def test_still_reads_a_macro_enabled_workbook_as_before() -> None:
    assert "sourceKind" not in holy_sheet.read(retyped(MACRO_DOC))


# ─── Hostile input ────────────────────────────────────────────────────────


def test_refuses_a_template_whose_content_type_claims_another_family() -> None:
    refused("family_mismatch", lambda: holy_sheet.read(retyped(PPT_TEMPLATE)))

    files = entries(xlsx())
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
        b"</Types>", f'<Override PartName="/ppt/presentation.xml" ContentType="{PPT_TEMPLATE}"/></Types>'.encode()
    )
    beside = zip_of(files)
    refused("family_mismatch", lambda: holy_sheet.read(beside))

    files = entries(xlsx())
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
        b'ContentType="application/xml"', f'ContentType="{TEMPLATE}"'.encode()
    )
    via_default = zip_of(files)
    refused("family_mismatch", lambda: holy_sheet.read(via_default))


def test_refuses_an_odf_spreadsheet_template_whose_body_is_text() -> None:
    refused("family_mismatch", lambda: holy_sheet.read(odf(OTS, "text")))


def test_refuses_an_odf_template_of_another_family_by_name() -> None:
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(odf("application/vnd.oasis.opendocument.text-template", "text"))


def test_refuses_an_odf_template_whose_manifest_names_another_family() -> None:
    refused("family_mismatch", lambda: holy_sheet.read(odf(OTS, "spreadsheet", "application/vnd.oasis.opendocument.text-template")))


def test_refuses_a_template_zip_bomb_reading_and_converting() -> None:
    bomb = retyped(TEMPLATE, {"xl/media/bomb.bin": bytes(65 * 1024 * 1024)})
    archive_refused("archive_limit", lambda: holy_sheet.read(bomb))
    archive_refused("archive_limit", lambda: holy_sheet.convert_template(bomb))


def test_refuses_a_template_with_a_traversing_entry_name_reading_and_converting() -> None:
    evil = retyped(TEMPLATE, {"../evil.xml": b"<x/>"})
    archive_refused("archive_path", lambda: holy_sheet.read(evil))
    archive_refused("archive_path", lambda: holy_sheet.convert_template(evil))


# ─── Converting ───────────────────────────────────────────────────────────


def test_converts_an_xltx_to_an_xlsx_changing_only_the_content_type_value() -> None:
    template = xltx()
    document = holy_sheet.convert_template(template, "document")
    before, after = entries(template), entries(document)
    assert list(after) == list(before)
    for name, data in before.items():
        if name == "[Content_Types].xml":
            assert after[name] == data.replace(TEMPLATE.encode(), DOC.encode())
        else:
            assert after[name] == data, f"{name} changed"
    assert "sourceKind" not in holy_sheet.read(document)


def test_round_trips_xlsx_to_xltx_and_back_with_every_part_byte_identical() -> None:
    document = xlsx()
    template = holy_sheet.convert_template(document, "template")
    back = holy_sheet.convert_template(template, "document")
    assert holy_sheet.read(template)["sourceKind"] == "template"
    assert entries(back) == entries(document)

    # Untouched parts are copied as RECORDS, not re-encoded: same compression.
    before, after = methods(document), methods(template)
    del before["[Content_Types].xml"], after["[Content_Types].xml"]
    assert after == before


def test_flips_direction_when_no_target_is_given_and_takes_a_path(tmp_path: Path) -> None:
    assert holy_sheet.read(holy_sheet.convert_template(xlsx()))["sourceKind"] == "template"
    path = tmp_path / "in.xltx"
    path.write_bytes(xltx())
    assert "sourceKind" not in holy_sheet.read(holy_sheet.convert_template(str(path)))
    assert "sourceKind" not in holy_sheet.read(holy_sheet.convert_template(path))


def test_converts_ods_to_ots_keeping_mimetype_first_stored_and_without_an_extra_field() -> None:
    ods = odf(ODS)
    ots = holy_sheet.convert_template(ods, "template")
    before, after = entries(ods), entries(ots)
    assert list(after) == list(before)
    assert next(iter(after)) == "mimetype"
    assert after["mimetype"] == OTS.encode()
    assert methods(ots)["mimetype"] == zipfile.ZIP_STORED
    assert ots[:4] == b"PK\x03\x04"
    assert struct.unpack("<H", ots[28:30])[0] == 0
    assert ots[30:38] == b"mimetype"
    assert ots[38 : 38 + len(OTS)] == OTS.encode()

    # ODF 1.3 Part 2 section 3.3: mimetype SHALL equal the manifest's root
    # media-type, so that one attribute value moves with it and nothing else.
    assert after["META-INF/manifest.xml"] == before["META-INF/manifest.xml"].replace(
        f'manifest:full-path="/" manifest:version="1.3" manifest:media-type="{ODS}"'.encode(),
        f'manifest:full-path="/" manifest:version="1.3" manifest:media-type="{OTS}"'.encode(),
    )
    for name, data in before.items():
        if name not in ("mimetype", "META-INF/manifest.xml"):
            assert after[name] == data, f"{name} changed"
    assert holy_sheet.read(ots)["sourceKind"] == "template"
    assert entries(holy_sheet.convert_template(ots, "document")) == before


def test_refuses_conversions_it_must_not_make() -> None:
    refused("already_template", lambda: holy_sheet.convert_template(xltx(), "template"))
    refused("already_document", lambda: holy_sheet.convert_template(xlsx(), "document"))
    refused("macro_enabled", lambda: holy_sheet.convert_template(retyped(MACRO_TEMPLATE), "document"))
    refused("macro_enabled", lambda: holy_sheet.convert_template(retyped(MACRO_DOC), "template"))
    refused("bad_target", lambda: holy_sheet.convert_template(xlsx(), "presentation"))
    refused("family_mismatch", lambda: holy_sheet.convert_template(retyped(PPT_TEMPLATE)))


def test_refuses_an_odf_package_whose_mimetype_is_not_stored_first() -> None:
    files = entries(odf(ODS))
    mimetype = files.pop("mimetype")
    files["mimetype"] = mimetype  # re-added LAST (still stored)
    late = zip_of(files)
    refused("odf_mimetype", lambda: holy_sheet.convert_template(late))


def test_refuses_a_content_type_declaration_it_cannot_rewrite_unambiguously() -> None:
    files = entries(xltx())
    files["[Content_Types].xml"] = files["[Content_Types].xml"].replace(
        b'<Override PartName="/xl/workbook.xml"',
        f'<!-- <Override PartName="/xl/workbook.xml" ContentType="{TEMPLATE}"/> --><Override PartName="/xl/workbook.xml"'.encode(),
    )
    decoy = zip_of(files)
    refused("not_convertible", lambda: holy_sheet.convert_template(decoy))


def test_names_a_word_processing_package_instead_of_converting_it() -> None:
    docx = zip_of({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"})
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.convert_template(docx)
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.convert_template(odf("application/vnd.oasis.opendocument.text", "text"))


# ─── CLI ──────────────────────────────────────────────────────────────────


def test_converts_through_the_template_convert_cli(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()

    def run(*args: str) -> int:
        return cli.main(list(args), out, err)

    (tmp_path / "in.xltx").write_bytes(xltx())
    assert run("template:convert", str(tmp_path / "in.xltx"), str(tmp_path / "out.xlsx")) == 0
    assert "document" in out.getvalue()
    assert "sourceKind" not in holy_sheet.read((tmp_path / "out.xlsx").read_bytes())

    assert run("template:convert", str(tmp_path / "out.xlsx"), str(tmp_path / "out.xlsx"), "--to=template") == 1
    assert "exists" in err.getvalue()

    assert run("template:convert", str(tmp_path / "out.xlsx"), str(tmp_path / "back.xltx"), "--to=template") == 0
    assert holy_sheet.read((tmp_path / "back.xltx").read_bytes())["sourceKind"] == "template"

    (tmp_path / "macro.xltm").write_bytes(retyped(MACRO_TEMPLATE))
    assert run("template:convert", str(tmp_path / "macro.xltm"), str(tmp_path / "macro.xlsx")) == 1
    assert "macro_enabled" in err.getvalue()
    assert not (tmp_path / "macro.xlsx").exists()

    assert run("template:convert") == 2
    assert "Usage" in err.getvalue()


def test_python_dash_m_runs_the_same_cli(tmp_path: Path) -> None:
    (tmp_path / "in.xltx").write_bytes(xltx())
    src = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    result = subprocess.run(
        [sys.executable, "-m", "holy_sheet", "template:convert", str(tmp_path / "in.xltx"), str(tmp_path / "out.xlsx")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "sourceKind" not in holy_sheet.read((tmp_path / "out.xlsx").read_bytes())


# ─── LibreOffice as an oracle (skipped where it is not installed) ─────────

_SOFFICE = next(
    (
        candidate
        for candidate in (
            os.environ.get("LIBREOFFICE_BIN"),
            "C:/Program Files/LibreOffice/program/soffice.com",
            "/usr/bin/soffice",
            "/usr/local/bin/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        )
        if candidate and os.path.isfile(candidate)
    ),
    None,
)


def _libreoffice(source: Path, fmt: str, out_dir: Path) -> Path:
    """Headless convert; the profile lives in a SHORT temp path (long ones crash LO on Windows)."""
    profile = (Path(tempfile.gettempdir()) / f"hplo{secrets.token_hex(3)}").as_posix().lstrip("/")
    result = subprocess.run(
        [_SOFFICE, f"-env:UserInstallation=file:///{profile}", "--headless", "--norestore",
         "--convert-to", fmt, "--outdir", str(out_dir), str(source)],
        capture_output=True, timeout=180,
    )
    out = out_dir / f"{source.stem}.{fmt.split(':')[0]}"
    assert result.returncode == 0
    assert out.is_file(), f"LibreOffice produced no {fmt}"
    return out


@pytest.mark.skipif(_SOFFICE is None, reason="LibreOffice is not installed; the oracle check runs where it is.")
def test_reads_what_libreoffice_writes_and_libreoffice_reads_our_conversions() -> None:
    work = Path(tempfile.mkdtemp(prefix="hpo"))
    try:
        (work / "src.xlsx").write_bytes(xlsx())
        for template, document in (("xltx", "xlsx"), ("ots", "ods")):
            lo_template = _libreoffice(work / "src.xlsx", template, work)
            read = holy_sheet.read(lo_template.read_bytes())
            assert read.get("sourceKind") == "template", f"LibreOffice .{template} not marked"
            assert "Rent" in json.dumps(read)

            (work / template).mkdir()
            lo_back = _libreoffice(lo_template, document, work / template)
            assert "sourceKind" not in holy_sheet.read(lo_back.read_bytes())

            (work / f"ours.{document}").write_bytes(holy_sheet.convert_template(lo_template.read_bytes(), "document"))
            (work / f"ours-{template}").mkdir()
            csv = _libreoffice(work / f"ours.{document}", "csv", work / f"ours-{template}")
            assert "Rent,1200" in csv.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
