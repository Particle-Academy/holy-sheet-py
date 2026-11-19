"""What a READ cell is decided by its XML, never by how its text looks.

Built from raw SpreadsheetML zipped in the test (no third-party writer):

- A text cell whose string starts with "=" (a shared string, ``t="s"``, or an
  inline string, ``t="inlineStr"``) has no ``<f>`` child, so it is text.
  ``read()`` keeps it as the cell's ``value`` and ``rows()`` returns it in
  both modes. Only a cell with an ``<f>`` child is a formula.
- An error cell (``t="e"``) reads as its error string, e.g. ``"#DIV/0!"``.
- A macro-enabled workbook (``.xlsm``: the main part declared
  ``application/vnd.ms-excel.sheet.macroEnabled.main+xml``, a
  ``vbaProject.bin`` beside it) reads its values; the macros are ignored.
"""

from __future__ import annotations

import io
import zipfile

import holy_sheet

SHEET_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _package(sheet_data: str, main_type: str = SHEET_MAIN, shared: list[str] | None = None, vba: bool = False) -> bytes:
    overrides = [
        f'<Override PartName="/xl/workbook.xml" ContentType="{main_type}"/>',
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>',
    ]
    rels = [
        f'<Relationship Id="rId1" Type="{REL_NS}/worksheet" Target="worksheets/sheet1.xml"/>',
    ]
    files: dict[str, bytes] = {}
    if shared is not None:
        overrides.append(
            '<Override PartName="/xl/sharedStrings.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        )
        rels.append(f'<Relationship Id="rId2" Type="{REL_NS}/sharedStrings" Target="sharedStrings.xml"/>')
        items = "".join(f"<si><t>{s}</t></si>" for s in shared)
        files["xl/sharedStrings.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{NS}" count="{len(shared)}" '
            f'uniqueCount="{len(shared)}">{items}</sst>'
        ).encode()
    defaults = (
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
    )
    if vba:
        defaults += '<Default Extension="bin" ContentType="application/vnd.ms-office.vbaProject"/>'
        rels.append(
            '<Relationship Id="rId3" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" '
            'Target="vbaProject.bin"/>'
        )
        files["xl/vbaProject.bin"] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 56

    files["[Content_Types].xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f"{defaults}{''.join(overrides)}</Types>"
    ).encode()
    files["_rels/.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG_REL}">'
        f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    ).encode()
    files["xl/workbook.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{NS}" xmlns:r="{REL_NS}">'
        '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ).encode()
    files["xl/_rels/workbook.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PKG_REL}">{"".join(rels)}</Relationships>'
    ).encode()
    files["xl/worksheets/sheet1.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>{sheet_data}</sheetData></worksheet>'
    ).encode()

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data)
    return out.getvalue()


# One row: a shared string, an inline string (both "=SUM(1)" as TEXT), and a
# real formula with a cached value.
TEXT_AND_FORMULA = (
    '<row r="1">'
    '<c r="A1" t="s"><v>0</v></c>'
    '<c r="B1" t="inlineStr"><is><t>=SUM(1)</t></is></c>'
    '<c r="C1"><f>SUM(1)</f><v>1</v></c>'
    "</row>"
)


def test_a_text_cell_starting_with_equals_reads_as_text_not_a_formula() -> None:
    sheet = holy_sheet.read(_package(TEXT_AND_FORMULA, shared=["=SUM(1)"]))["sheets"][0]

    assert sheet["cells"]["A1"]["value"] == "=SUM(1)"
    assert "formula" not in sheet["cells"]["A1"]
    assert sheet["cells"]["B1"]["value"] == "=SUM(1)"
    assert "formula" not in sheet["cells"]["B1"]
    assert sheet["cells"]["C1"]["formula"] == "SUM(1)"


def test_rows_returns_equals_text_as_text_in_both_modes_and_only_f_cells_as_formulas() -> None:
    sheet = holy_sheet.read(_package(TEXT_AND_FORMULA, shared=["=SUM(1)"]))["sheets"][0]

    assert holy_sheet.rows(sheet) == [["=SUM(1)", "=SUM(1)", 1]]
    assert holy_sheet.rows(sheet, formulas="formula") == [["=SUM(1)", "=SUM(1)", "=SUM(1)"]]


def test_an_error_cell_reads_as_its_error_string() -> None:
    data = _package('<row r="1"><c r="A1" t="e"><v>#DIV/0!</v></c><c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c></row>')
    sheet = holy_sheet.read(data)["sheets"][0]

    assert sheet["cells"]["A1"]["value"] == "#DIV/0!"
    assert sheet["cells"]["B1"]["computedValue"] == "#DIV/0!"
    assert holy_sheet.rows(sheet) == [["#DIV/0!", "#DIV/0!"]]
    assert holy_sheet.rows(sheet, formulas="formula") == [["#DIV/0!", "=1/0"]]


def test_reads_the_values_of_a_macro_enabled_workbook_and_ignores_the_macros() -> None:
    data = _package(TEXT_AND_FORMULA, main_type=XLSM_MAIN, shared=["=SUM(1)"], vba=True)
    schema = holy_sheet.read(data)

    assert "sourceKind" not in schema
    assert holy_sheet.rows(schema["sheets"][0]) == [["=SUM(1)", "=SUM(1)", 1]]
