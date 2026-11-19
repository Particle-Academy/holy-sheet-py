"""CC-fancy-0050: read() names the workbook's active sheet, the writer emits it
when a schema names one, and the `formulas` write option turns off the
promotion of a bare "=" string.

Driven by the PHP repo's tests/fixtures/active-sheet/cases.json, the same bytes
and hand-written expectations all three ports pass. Found beside the PHP
sources like the read-option fixtures; missing is an error, never a skip. The
options there are PHP's (`{sheet}`, `{formulas}`); this port takes them as
keyword arguments.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from holy_sheet import describe, read, to_bytes, validate, write
from tests import _oracle
from tests.fixtures import META


def _dir() -> Path:
    src = _oracle.php_src_root()
    path = src.parent / "tests" / "fixtures" / "active-sheet" if src is not None else None
    if path is None or not (path / "cases.json").is_file():
        raise RuntimeError(
            "active-sheet fixtures not found. They live in the PHP holy-sheet repo at "
            "tests/fixtures/active-sheet; check it out beside this one or set HOLY_SHEET_PHP_SRC."
        )
    return path


DIR = _dir()
CASES: dict[str, Any] = json.loads((DIR / "cases.json").read_text(encoding="utf-8"))


def _fixture(name: str) -> bytes:
    return (DIR / name).read_bytes()


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _book_views(data: bytes) -> str | None:
    xml = _parts(data)["xl/workbook.xml"].decode("utf-8")
    match = re.search(r"<bookViews>.*?</bookViews>", xml)
    return match.group(0) if match else None


def _value_and_formula(cells: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Each expected cell's `value` and `formula` as read, in the expected
    order. Formats are the writer's business, not what these cases are about."""
    out: dict[str, Any] = {}
    for address in expected:
        cell = cells.get(address, {})
        out[address] = {k: cell[k] for k in ("value", "formula") if k in cell}
    return out


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in cases]


def test_has_cases_to_run() -> None:
    assert len(CASES["read"]) > 15
    assert len(CASES["write"]) > 3
    assert len(CASES["invalid"]) > 3
    assert len(CASES["formulas"]) == 3
    assert len(CASES["badOptions"]) == 3


@pytest.mark.parametrize("case", CASES["read"], ids=_ids(CASES["read"]))
def test_reads_the_active_sheet_of_each_shared_fixture(case: dict[str, Any]) -> None:
    schema = read(_fixture(case["file"]), **case["options"])

    assert schema.get("activeSheet") == case["activeSheet"]


def test_describes_the_active_sheet_from_a_path_too() -> None:
    assert describe(str(DIR / "active-1.xlsx"))["activeSheet"] == {"index": 1, "name": "Second"}


def test_places_active_sheet_right_after_sheets() -> None:
    assert list(read(_fixture("active-1.xlsx")))[:2] == ["sheets", "activeSheet"]


@pytest.mark.parametrize("case", CASES["write"], ids=_ids(CASES["write"]))
def test_writes_the_active_sheet_each_shared_case_names(case: dict[str, Any]) -> None:
    data = to_bytes(case["schema"])

    assert _book_views(data) == case["bookViews"]
    assert read(data)["activeSheet"] == case["activeSheet"]


@pytest.mark.parametrize("case", CASES["invalid"], ids=_ids(CASES["invalid"]))
def test_refuses_each_shared_invalid_active_sheet(case: dict[str, Any]) -> None:
    assert validate(case["schema"]) == case["errors"]


def test_round_trips_a_read_workbook_with_its_active_sheet() -> None:
    described = read(_fixture("active-2.xlsx"))

    assert validate(described) == []
    assert read(to_bytes(described))["activeSheet"] == {"index": 2, "name": "Third"}


@pytest.mark.parametrize("case", CASES["formulas"], ids=_ids(CASES["formulas"]))
def test_writes_each_formulas_mode_as_the_shared_case_reads_it_back(case: dict[str, Any]) -> None:
    got = read(to_bytes(CASES["formulaSchema"], **case["options"]))

    assert _value_and_formula(got["sheets"][0]["cells"], case["cells"]) == case["cells"]
    assert _value_and_formula(got["sheets"][1]["cells"], case["rows"]) == case["rows"]


def test_writes_byte_identical_parts_under_promote_and_under_no_option() -> None:
    schema = CASES["formulaSchema"]

    assert _parts(to_bytes(schema, formulas="promote")) == _parts(to_bytes(schema))


def test_takes_the_formulas_option_on_write() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "explicit.xlsx")
        write(CASES["formulaSchema"], path, formulas="explicit")
        assert describe(path)["sheets"][0]["cells"]["A1"]["value"] == "=SUM(B1:B2)"


@pytest.mark.parametrize("case", CASES["badOptions"], ids=_ids(CASES["badOptions"]))
def test_refuses_each_shared_bad_write_option_before_writing(case: dict[str, Any]) -> None:
    schema = CASES["formulaSchema"]
    if "formulas" in case["options"]:
        with pytest.raises(ValueError) as raised:
            to_bytes(schema, **case["options"])
        assert str(raised.value) == case["message"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "never.xlsx")
            with pytest.raises(ValueError):
                write(schema, path, **case["options"])
            assert not os.path.exists(path)
    else:
        # An unknown option is an unknown keyword argument: Python's own TypeError.
        with pytest.raises(TypeError):
            to_bytes(schema, **case["options"])


def test_checks_the_option_before_the_schema() -> None:
    with pytest.raises(ValueError):
        to_bytes({"sheets": []}, formulas="nope")


# --- parity with the PHP reference -------------------------------------------


def _write_payloads() -> list[tuple[str, Any, dict[str, Any]]]:
    out: list[tuple[str, Any, dict[str, Any]]] = []
    for case in CASES["write"]:
        # docProps/core.xml is a clock unless meta is pinned.
        out.append((case["name"], {"meta": META, **case["schema"]}, {}))
    for case in CASES["formulas"]:
        out.append((case["name"], CASES["formulaSchema"], case["options"]))
    return out


PAYLOADS = _write_payloads()


@pytest.mark.parity
@pytest.mark.parametrize("name,payload,options", PAYLOADS, ids=[p[0] for p in PAYLOADS])
def test_emits_the_same_parts_as_php(php_oracle, name: str, payload: Any, options: dict[str, Any]) -> None:
    php_parts = php_oracle.text_parts(php_oracle.php_to_bytes(payload, options))
    py_parts = php_oracle.text_parts(to_bytes(payload, **options))

    assert sorted(py_parts) == sorted(php_parts)
    for part in sorted(php_parts):
        assert py_parts[part] == php_parts[part], f"{part} differs from PHP for {name!r}"


# php_describe takes no options; the cases with a `sheet` option are covered by
# the shared expectation above.
WHOLE_READS = [c for c in CASES["read"] if not c["options"]]


@pytest.mark.parity
@pytest.mark.parametrize("case", WHOLE_READS, ids=_ids(WHOLE_READS))
def test_reads_each_fixture_as_php_does(php_oracle, case: dict[str, Any]) -> None:
    assert read(_fixture(case["file"])) == php_oracle.php_describe(DIR / case["file"])
