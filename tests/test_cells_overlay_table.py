"""A sheet with BOTH a table (`columns` + `rows`) and `cells`.

Until this fix the Normalizer returned early the moment `cells` was set, and
the table was silently dropped: the schema below wrote 2 cells here and every
table cell in PHP and Node. `validate()` reported nothing, because the
Validator explicitly permits both together.

PHP and Node build the table first and then OVERLAY the explicit cells: a cell
wins at its address, and its format is MERGED onto the one beneath it
(`beneath.merge_with(cell)`), so a cell that only sets `bold` keeps the
column's currency format and the theme's banding. See PHP
`Schema/Normalizer.php` and Node `src/schema/normalizer.ts`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import holy_sheet
from holy_sheet.schema.normalizer import Normalizer
from holy_sheet.workbook.cell_format import CellFormat
from tests import _oracle
from tests.fixtures import SCHEMAS

SCHEMA = SCHEMAS["cellsOverTable"]

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_SCRIPT = REPO_ROOT / "scripts" / "node_tobytes.mjs"


def _sheet():
    return Normalizer().normalize(SCHEMA).sheets[0]


def test_a_sheet_with_columns_rows_and_cells_writes_every_value() -> None:
    cells = holy_sheet.read(holy_sheet.to_bytes(SCHEMA))["sheets"][0]["cells"]
    values = {address: cell["value"] for address, cell in cells.items()}

    assert values == {
        "A1": "Region",
        "B1": "Revenue",
        "A2": "North",
        "B2": 100,
        "A3": "South",
        "B3": 250,
        "D1": "Note",
    }


def test_the_normalized_sheet_keeps_the_table_and_adds_the_cells() -> None:
    assert sorted(_sheet().cells) == ["A1", "A2", "A3", "B1", "B2", "B3", "D1"]


def test_an_overlaid_cell_overrides_the_table_cell_and_merges_its_format() -> None:
    cell = _sheet().cells["B3"]

    # The overlay's value wins at its address.
    assert cell.value == 250

    # The format beneath was the currency column merged with the theme's band
    # for the second data row; the overlay's `bold` merges on top of it.
    beneath = CellFormat(display_format="currency", decimals=2, currency="USD").merge_with(
        CellFormat(background_color="#F3F4F6")
    )
    assert cell.format == beneath.merge_with(CellFormat(bold=True))
    assert cell.format.bold is True
    assert cell.format.display_format == "currency"
    assert cell.format.currency == "USD"
    assert cell.format.background_color == "#F3F4F6"


def test_a_cell_with_nothing_beneath_keeps_its_own_format() -> None:
    cell = _sheet().cells["D1"]

    assert cell.value == "Note"
    assert cell.format == CellFormat(italic=True)


def test_a_cells_only_sheet_is_unchanged() -> None:
    sheet = Normalizer().normalize(SCHEMAS["sparse"]).sheets[0]

    assert sorted(sheet.cells) == ["A1", "A2", "B1", "B2", "B3", "B4", "C1", "D1"]


# --- Cross-twin parity -----------------------------------------------------
#
# The PHP half of this also runs in `test_parity_php.py` (the fixture is in the
# shared SCHEMAS table, so CI byte-compares every part against PHP). Here the
# same schema is written by all three engines and compared both as parts and as
# the cell set read back, so a failure names the cell rather than the part.


def _node_binary() -> str | None:
    return shutil.which("node")


def _node_dist() -> Path:
    explicit = os.environ.get("HOLY_SHEET_JS_DIST")
    if explicit:
        return Path(explicit)
    return REPO_ROOT.parent / "holy-sheet-js" / "dist" / "index.js"


def _node_to_bytes(payload: object) -> bytes:
    with tempfile.TemporaryDirectory(prefix="holy-sheet-node-parity-") as tmp:
        json_path = Path(tmp) / "input.json"
        out_path = Path(tmp) / "out.xlsx"
        json_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [_node_binary() or "node", str(NODE_SCRIPT), str(json_path), str(out_path)],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"the Node oracle exited {result.returncode}: "
                f"{result.stderr.decode('utf-8', 'replace')[:2000]}"
            )
        return out_path.read_bytes()


def _cell_set(data: bytes) -> dict:
    return holy_sheet.read(data)["sheets"][0]["cells"]


@pytest.mark.parity
def test_php_writes_the_same_cell_set(php_oracle) -> None:
    php_bytes = php_oracle.php_to_bytes(SCHEMA)
    py_bytes = holy_sheet.to_bytes(SCHEMA)

    assert _cell_set(py_bytes) == _cell_set(php_bytes)
    assert len(_cell_set(php_bytes)) == 7
    assert php_oracle.text_parts(py_bytes) == php_oracle.text_parts(php_bytes)


@pytest.mark.parity
def test_node_writes_the_same_cell_set() -> None:
    if _node_binary() is None or not _node_dist().is_file():
        if os.environ.get("CI") and os.environ.get("HOLY_SHEET_JS_DIST"):
            raise RuntimeError("HOLY_SHEET_JS_DIST is set but the Node oracle cannot run")
        pytest.skip(f"Node oracle unavailable: need node on PATH and {_node_dist()}")

    node_bytes = _node_to_bytes(SCHEMA)
    py_bytes = holy_sheet.to_bytes(SCHEMA)

    assert _cell_set(py_bytes) == _cell_set(node_bytes)
    assert len(_cell_set(node_bytes)) == 7
    assert _oracle.text_parts(py_bytes) == _oracle.text_parts(node_bytes)
