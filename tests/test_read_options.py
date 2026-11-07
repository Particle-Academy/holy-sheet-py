"""CC-fancy-0012 (sheet selection, the rows view, border styles) and
CC-fancy-0013 (read limits).

The read cases come from the PHP repo's tests/fixtures/read-options/cases.json:
the same bytes and the same hand-written expectations all three ports pass.
Found beside the PHP sources like the ODS fixtures; missing is an error, never
a skip. The options there are PHP's (`{sheet, limits}`, `{formulas,
maxCells}`); this port takes them as keyword arguments.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

import holy_sheet
from holy_sheet import (
    DEFAULT_READ_LIMITS,
    ArchiveRefused,
    ReadLimitExceeded,
    SheetNotFoundException,
    describe,
    diff,
    read,
    rows,
    to_bytes,
    validate,
)
from tests import _oracle


def _dir() -> Path:
    src = _oracle.php_src_root()
    path = src.parent / "tests" / "fixtures" / "read-options" if src is not None else None
    if path is None or not (path / "cases.json").is_file():
        raise RuntimeError(
            "read-option fixtures not found. They live in the PHP holy-sheet repo at "
            "tests/fixtures/read-options; check it out beside this one or set HOLY_SHEET_PHP_SRC."
        )
    return path


DIR = _dir()
CASES: dict[str, Any] = json.loads((DIR / "cases.json").read_text(encoding="utf-8"))


def _fixture(name: str) -> bytes:
    return (DIR / name).read_bytes()


def _read_kwargs(options: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in options.items()}


def _rows_kwargs(options: dict[str, Any]) -> dict[str, Any]:
    names = {"formulas": "formulas", "maxCells": "max_cells"}
    return {names[k]: v for k, v in options.items()}


def _outcome(run) -> dict[str, Any]:
    try:
        return {"value": run()}
    except ReadLimitExceeded as e:
        return {"refused": {"reason": e.reason, "limit": e.limit, "max": e.max, "actual": e.actual, "message": str(e)}}
    except ArchiveRefused as e:
        return {"refused": {"reason": e.reason}}
    except SheetNotFoundException as e:
        return {"sheetNotFound": {"sheet": e.sheet, "available": e.available, "message": str(e)}}


def test_has_cases_to_run() -> None:
    assert len(CASES["read"]) > 20
    assert len(CASES["rows"]) > 4


@pytest.mark.parametrize("case", CASES["read"], ids=[c["name"] for c in CASES["read"]])
def test_reads_each_shared_case_as_written(case: dict[str, Any]) -> None:
    got = _outcome(lambda: read(_fixture(case["file"]), **_read_kwargs(case["options"])))
    if "refused" in case:
        assert got.get("refused") == case["refused"]
    elif "sheetNotFound" in case:
        assert got.get("sheetNotFound") == case["sheetNotFound"]
    else:
        assert [s["name"] for s in got["value"]["sheets"]] == case["sheets"]


@pytest.mark.parametrize("case", CASES["rows"], ids=[c["name"] for c in CASES["rows"]])
def test_turns_each_shared_case_into_rows(case: dict[str, Any]) -> None:
    got = _outcome(lambda: rows(case["sheet"], **_rows_kwargs(case["options"])))
    if "refused" in case:
        assert got.get("refused") == case["refused"]
    else:
        assert got["value"] == case["rows"]
        # PHP's identical(): an int stays an int and a bool a bool.
        assert json.dumps(got["value"]) == json.dumps(case["rows"])


def test_pins_the_default_limits_the_ports_share() -> None:
    assert DEFAULT_READ_LIMITS == CASES["defaultLimits"]


def test_is_still_an_archive_refused() -> None:
    with pytest.raises(ArchiveRefused):
        read(_fixture("two-sheets.xlsx"), limits={"maxRows": 1})


def test_reads_only_the_selected_sheet_with_its_cells() -> None:
    schema = read(_fixture("two-sheets.xlsx"), sheet="Summary")
    assert [s["name"] for s in schema["sheets"]] == ["Summary"]
    assert schema["sheets"][0]["cells"] == {"A1": {"value": 1}, "B1": {"value": 2}}


def test_takes_the_same_options_from_a_path() -> None:
    path = str(DIR / "two-sheets.xlsx")
    assert [s["name"] for s in describe(path, sheet="Data")["sheets"]] == ["Data"]
    with pytest.raises(ReadLimitExceeded):
        describe(path, limits={"maxRows": 1})


def test_refuses_a_sheet_that_is_not_a_name_and_bad_rows_options() -> None:
    data = _fixture("two-sheets.xlsx")
    with pytest.raises(TypeError, match="sheet must be a sheet name"):
        read(data, sheet=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="formulas must be"):
        rows({"cells": {}}, formulas="cached")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_cells must be"):
        rows({"cells": {}}, max_cells=True)  # type: ignore[arg-type]


def test_reads_a_writer_round_trip_into_rows() -> None:
    data = to_bytes(
        {"sheets": [{"name": "T", "columns": [{"header": "Name"}, {"header": "Qty", "type": "integer"}], "rows": [["a", 1], ["b", 2]]}]}
    )
    sheet = read(data, sheet="T")["sheets"][0]
    assert rows(sheet) == [["Name", "Qty"], ["a", 1], ["b", 2]]


def test_message_is_plain_words() -> None:
    with pytest.raises(ReadLimitExceeded) as caught:
        read(_fixture("two-sheets.xlsx"), limits={"maxRows": 2})
    assert str(caught.value) == "Spreadsheet refused: a sheet has 3 rows, over the limit of 2 rows (maxRows) by 1."


# --- border styles ------------------------------------------------------------


def _styles_xml(fmt: dict[str, Any]) -> str:
    data = to_bytes({"sheets": [{"name": "B", "cells": {"A1": {"value": 1, "format": fmt}}}]})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("xl/styles.xml").decode("utf-8")


def test_writes_a_bare_colour_as_a_thin_border_exactly_as_before() -> None:
    assert (
        '<border><left/><right/><top style="thin"><color rgb="FFFF0000"/></top><bottom/><diagonal/></border>'
        in _styles_xml({"borderTop": "#FF0000"})
    )


@pytest.mark.parametrize("style", ["thin", "medium", "thick", "dashed", "dotted", "double"])
def test_writes_each_border_style_per_side(style: str) -> None:
    xml = _styles_xml({"borderBottom": {"style": style, "color": "#00AA00"}, "borderLeft": "#000000"})
    assert (
        f'<border><left style="thin"><color rgb="FF000000"/></left><right/><top/><bottom style="{style}">'
        '<color rgb="FF00AA00"/></bottom><diagonal/></border>'
    ) in xml


def test_defaults_an_object_border_to_thin_and_black() -> None:
    assert '<right style="dashed"><color rgb="FF000000"/></right>' in _styles_xml({"borderRight": {"style": "dashed"}})
    assert '<right style="thin"><color rgb="FF000000"/></right>' in _styles_xml({"borderRight": {}})
    assert '<right style="thin"><color rgb="FF000000"/></right>' in _styles_xml({"borderRight": []})


def test_dedupes_a_thin_object_border_with_the_bare_colour() -> None:
    data = to_bytes(
        {
            "sheets": [
                {
                    "name": "B",
                    "cells": {
                        "A1": {"value": 1, "format": {"borderTop": "#FF0000"}},
                        "A2": {"value": 2, "format": {"borderTop": {"style": "thin", "color": "#FF0000"}}},
                    },
                }
            ]
        }
    )
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert '<borders count="2">' in z.read("xl/styles.xml").decode("utf-8")


def test_reads_a_border_style_back_and_thin_as_the_bare_colour() -> None:
    schema = read(
        to_bytes(
            {
                "sheets": [
                    {
                        "name": "B",
                        "cells": {
                            "A1": {"value": 1, "format": {"borderTop": "#FF0000", "borderBottom": {"style": "double", "color": "#0000FF"}}},
                            "A2": {"value": 2, "format": {"borderLeft": {"style": "thin", "color": "#00FF00"}}},
                        },
                    }
                ]
            }
        )
    )
    a1 = schema["sheets"][0]["cells"]["A1"]["format"]
    assert a1["borderTop"] == "#FF0000"
    assert a1["borderBottom"] == {"style": "double", "color": "#0000FF"}
    assert schema["sheets"][0]["cells"]["A2"]["format"]["borderLeft"] == "#00FF00"


def test_reads_a_style_outside_the_six_as_thin() -> None:
    data = to_bytes({"sheets": [{"name": "B", "cells": {"A1": {"value": 1, "format": {"borderTop": {"style": "dashed", "color": "#FF0000"}}}}}]})
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            body = src.read(name)
            if name == "xl/styles.xml":
                body = body.replace(b'style="dashed"', b'style="mediumDashDot"')
            dst.writestr(name, body)
    assert read(out.getvalue())["sheets"][0]["cells"]["A1"]["format"]["borderTop"] == "#FF0000"


def test_round_trips_styled_borders_as_the_same_workbook() -> None:
    schema = {"sheets": [{"name": "B", "cells": {"A1": {"value": 1, "format": {"borderTop": {"style": "medium", "color": "#123456"}}}}}]}
    assert diff(schema, read(to_bytes(schema))) == []


def test_validates_a_border_as_a_colour_or_style_and_color() -> None:
    def errors(border: Any) -> list[dict[str, Any]]:
        return validate({"sheets": [{"name": "B", "cells": {"A1": {"value": 1, "format": {"borderTop": border}}}}]})

    assert errors("#000000") == []
    assert errors({"style": "thick", "color": "#00ff00"}) == []
    assert errors({"style": "dotted"}) == []
    bad = errors({"style": "zigzag"})
    assert len(bad) == 1
    assert bad[0]["path"] == "sheets[0].cells.A1.format.borderTop.style"
    assert "thin, medium, thick, dashed, dotted, double" in bad[0]["hint"]
    assert errors({"style": "thin", "color": "red"})[0]["path"] == "sheets[0].cells.A1.format.borderTop.color"
    assert errors({"style": "thin", "width": 2})[0]["path"] == "sheets[0].cells.A1.format.borderTop.width"
    assert errors(5)[0]["path"] == "sheets[0].cells.A1.format.borderTop"
    assert errors(["thin"])[0]["path"] == "sheets[0].cells.A1.format.borderTop"
    assert errors(True)[0]["path"] == "sheets[0].cells.A1.format.borderTop"


def test_validates_borders_in_the_row_form_too() -> None:
    errs = validate({"sheets": [{"name": "B", "rows": [[{"value": 1, "format": {"borderLeft": {"style": "wavy"}}}]]}]})
    assert len(errs) == 1
    assert errs[0]["path"] == "sheets[0].rows[0][0].format.borderLeft.style"


def test_exports_the_new_names() -> None:
    for name in ("rows", "to_csv", "ReadLimitExceeded", "SheetNotFoundException", "OptionsInvalid", "DEFAULT_READ_LIMITS"):
        assert name in holy_sheet.__all__
