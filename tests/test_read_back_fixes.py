"""Four read/write defects a consumer (LiftPoint) hit. Mirrors PHP
tests/Unit/ReadBackFixesTest.php case for case:

- the 1904 date system (`<workbookPr date1904="1"/>`) was ignored, so every
  date in such a workbook read 1462 days early;
- a rich inline string (`<is><r><t>..</t></r></is>`) read as None;
- a date-formatted formula's cached value came back as a raw serial;
- a column's custom `format` code was ignored by the normalizer.

Fixtures are built here, from the writer's own output with the parts a producer
like Excel writes patched in, so nothing depends on a binary file.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from typing import Any

import pytest

import holy_sheet


def zip_of(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return out.getvalue()


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def parts_of(schema: dict[str, Any]) -> dict[str, bytes]:
    return unzip(holy_sheet.to_bytes(schema))


def base_parts() -> dict[str, bytes]:
    """A1 a date, A2 a datetime, A3 a date formula, A4 a plain number formula, A5 an inline string."""
    return parts_of(
        {
            "sheets": [
                {
                    "name": "S",
                    "cells": {
                        "A1": {"value": "2026-11-07", "format": {"displayFormat": "date"}},
                        "A2": {"value": "2026-11-07T12:00:00Z", "format": {"displayFormat": "datetime"}},
                        "A3": {"formula": "A1+1", "computedValue": 46334, "format": {"displayFormat": "date"}},
                        "A4": {"formula": "1+1", "computedValue": 2},
                        "A5": "plain",
                    },
                }
            ]
        }
    )


def with_date1904(parts: dict[str, bytes], flag: str) -> dict[str, bytes]:
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"<sheets>", b'<workbookPr date1904="' + flag.encode() + b'"/><sheets>'
    )
    return parts


def parts_1904(flag: str = "1") -> dict[str, bytes]:
    """The same workbook in the 1904 date system: the flag, and every serial 1462 days smaller."""
    parts = with_date1904(base_parts(), flag)
    sheet = parts["xl/worksheets/sheet1.xml"]
    for old, new in ((b"<v>46333</v>", b"<v>44871</v>"), (b"<v>46333.5</v>", b"<v>44871.5</v>"), (b"<v>46334</v>", b"<v>44872</v>")):
        sheet = sheet.replace(old, new)
    parts["xl/worksheets/sheet1.xml"] = sheet
    return parts


def cells_of(parts: dict[str, bytes]) -> dict[str, Any]:
    return holy_sheet.read(zip_of(parts))["sheets"][0]["cells"]


# ─── The 1904 date system ─────────────────────────────────────────────────────


def test_the_fixture_holds_the_1900_serials_the_1904_copy_rewrites() -> None:
    sheet = base_parts()["xl/worksheets/sheet1.xml"]
    assert b"<v>46333</v>" in sheet and b"<v>46333.5</v>" in sheet and b"<v>46334</v>" in sheet


@pytest.mark.parametrize("flag", ["1", "true"])
def test_reads_a_date_from_a_workbook_that_declares_date1904(flag: str) -> None:
    cells = cells_of(parts_1904(flag))
    assert cells["A1"]["value"] == "2026-11-07"
    assert cells["A2"]["value"] == "2026-11-07T12:00:00Z"


@pytest.mark.parametrize("flag", ["0", "false", "absent"])
def test_reads_the_1900_system_when_date1904_is_off_or_absent(flag: str) -> None:
    parts = base_parts() if flag == "absent" else with_date1904(base_parts(), flag)
    cells = cells_of(parts)
    assert cells["A1"]["value"] == "2026-11-07"
    assert cells["A2"]["value"] == "2026-11-07T12:00:00Z"


def test_converts_the_cached_value_of_a_date_formula_with_the_same_epoch() -> None:
    assert cells_of(parts_1904())["A3"]["computedValue"] == "2026-11-08"


def test_writes_a_read_1904_workbook_back_as_the_same_dates_in_the_1900_system() -> None:
    again = parts_of(holy_sheet.read(zip_of(parts_1904())))
    assert b"date1904" not in again["xl/workbook.xml"]
    assert b'<c r="A1" s="1"><v>46333</v></c>' in again["xl/worksheets/sheet1.xml"]


# ─── The cached value of a date-formatted formula ─────────────────────────────


def test_a_date_formula_cached_value_reads_as_the_iso_date_not_a_serial() -> None:
    cells = cells_of(base_parts())
    assert cells["A3"]["formula"] == "A1+1"
    assert cells["A3"]["computedValue"] == "2026-11-08"


def test_a_datetime_formula_cached_value_reads_as_an_iso_datetime() -> None:
    parts = parts_of(
        {
            "sheets": [
                {
                    "name": "S",
                    "cells": {"A1": {"formula": "NOW()", "computedValue": 46333.25, "format": {"displayFormat": "datetime"}}},
                }
            ]
        }
    )
    assert cells_of(parts)["A1"]["computedValue"] == "2026-11-07T06:00:00Z"


def test_a_cached_value_stays_a_number_when_the_formula_is_not_date_formatted() -> None:
    value = cells_of(base_parts())["A4"]["computedValue"]
    assert value == 2 and not isinstance(value, bool)


def test_an_iso_cached_value_is_written_back_as_the_serial() -> None:
    parts = parts_of(
        {
            "sheets": [
                {
                    "name": "S",
                    "cells": {"A1": {"formula": "A2+1", "computedValue": "2026-11-08", "format": {"displayFormat": "date"}}},
                }
            ]
        }
    )
    assert b'<c r="A1" s="1"><f>A2+1</f><v>46334</v></c>' in parts["xl/worksheets/sheet1.xml"]


def test_a_date_formula_survives_a_read_and_a_write_unchanged() -> None:
    again = parts_of(holy_sheet.read(zip_of(base_parts())))
    assert b"<f>A1+1</f><v>46334</v>" in again["xl/worksheets/sheet1.xml"]
    assert cells_of(again)["A3"]["computedValue"] == "2026-11-08"


# ─── A rich inline string ─────────────────────────────────────────────────────


def test_a_rich_inline_string_reads_as_the_text_of_its_runs() -> None:
    parts = base_parts()
    rich = b'<c r="A5" t="inlineStr"><is><r><t xml:space="preserve">Hello </t></r><r><rPr><b/></rPr><t>world</t></r></is></c>'
    sheet, count = re.subn(rb'<c r="A5"[^>]*><is>.*?</is></c>', rich, parts["xl/worksheets/sheet1.xml"], count=1)
    assert count == 1
    parts["xl/worksheets/sheet1.xml"] = sheet
    assert cells_of(parts)["A5"]["value"] == "Hello world"


def test_a_plain_inline_string_still_reads() -> None:
    assert cells_of(base_parts())["A5"]["value"] == "plain"


# ─── A column format code ─────────────────────────────────────────────────────


def num_fmt_of(parts: dict[str, bytes], code: str, ref: str) -> tuple[str | None, str | None]:
    """(the numFmt id written for `code`, the numFmt id of the xf cell `ref` uses)."""
    styles = parts["xl/styles.xml"].decode()
    sheet = parts["xl/worksheets/sheet1.xml"].decode()
    escaped = html.escape(code, quote=True).replace("&#x27;", "&apos;")
    fmt = re.search(r'<numFmt numFmtId="(\d+)" formatCode="' + re.escape(escaped) + r'"/>', styles)
    cell = re.search(r'<c r="' + ref + r'" s="(\d+)"', sheet)
    xfs = re.search(r"<cellXfs[^>]*>(.*)</cellXfs>", styles)
    ids = re.findall(r'<xf [^>]*numFmtId="(\d+)"', xfs.group(1) if xfs else "")
    used = ids[int(cell.group(1))] if cell and int(cell.group(1)) < len(ids) else None
    return (fmt.group(1) if fmt else None, used)


def test_a_column_format_code_is_written_as_a_custom_numfmt_and_applied_to_the_column() -> None:
    code = '#,##0.000" kg"'
    parts = parts_of(
        {"sheets": [{"name": "S", "columns": [{"header": "Weight", "type": "number", "format": code}], "rows": [[1.5], [2.25]]}]}
    )
    written, a2 = num_fmt_of(parts, code, "A2")
    _, a3 = num_fmt_of(parts, code, "A3")
    assert written is not None
    assert a2 == written and a3 == written


def test_a_column_format_code_overrides_the_type_code_but_the_type_decides_the_value() -> None:
    parts = parts_of(
        {"sheets": [{"name": "S", "columns": [{"header": "When", "type": "date", "format": "dd/mm/yyyy"}], "rows": [["2026-11-07"]]}]}
    )
    written, a2 = num_fmt_of(parts, "dd/mm/yyyy", "A2")
    assert a2 is not None and a2 == written
    assert b"yyyy-mm-dd" not in parts["xl/styles.xml"]
    assert b"<v>46333</v>" in parts["xl/worksheets/sheet1.xml"]


def test_a_column_format_code_applies_to_an_auto_column_too() -> None:
    parts = parts_of({"sheets": [{"name": "S", "columns": [{"header": "Ratio", "format": "0.0000"}], "rows": [[0.5]]}]})
    written, a2 = num_fmt_of(parts, "0.0000", "A2")
    assert written is not None and a2 == written


def test_a_column_format_code_carries_onto_the_totals_row() -> None:
    parts = parts_of(
        {
            "sheets": [
                {
                    "name": "S",
                    "columns": [{"header": "Weight", "type": "number", "format": "0.000"}],
                    "rows": [[1.5], [2.5]],
                    "totals": {"Weight": "sum"},
                }
            ]
        }
    )
    written, total = num_fmt_of(parts, "0.000", "A4")
    assert total is not None and total == written


def test_a_column_format_code_gives_way_to_a_display_format_a_cell_sets_itself() -> None:
    parts = parts_of(
        {
            "sheets": [
                {
                    "name": "S",
                    "columns": [{"header": "Weight", "type": "number", "format": "0.000"}],
                    "rows": [[{"value": 0.5, "format": {"displayFormat": "percentage"}}]],
                }
            ]
        }
    )
    assert b'formatCode="0.000"' not in parts["xl/styles.xml"]
    assert b'formatCode="0.0%"' in parts["xl/styles.xml"]


@pytest.mark.parametrize("fmt", ["", 12, "0" * 256], ids=["empty", "a number", "too long"])
def test_the_validator_refuses_a_column_format_that_cannot_be_written(fmt: Any) -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "S", "columns": [{"header": "X", "format": fmt}], "rows": [[1]]}]})
    assert "sheets[0].columns[0].format" in [e["path"] for e in errors]


def test_the_validator_accepts_a_column_format_that_is_a_code() -> None:
    assert holy_sheet.validate({"sheets": [{"name": "S", "columns": [{"header": "X", "format": "0.00"}], "rows": [[1]]}]}) == []


def test_the_column_format_error_matches_php_word_for_word() -> None:
    errors = holy_sheet.validate({"sheets": [{"name": "S", "columns": [{"header": "X", "format": ""}], "rows": [[1]]}]})
    error = next(e for e in errors if e["path"] == "sheets[0].columns[0].format")
    assert error["expected"] == "a non-empty number-format code of at most 255 bytes"
    assert error["got"] == "string"
    assert error["hint"] == (
        'A custom Excel number-format code applied to every cell in the column, like "0.000" or '
        '"dd/mm/yyyy". It overrides the format the column type would give.'
    )
