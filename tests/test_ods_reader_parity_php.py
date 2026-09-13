"""Cross-runtime ODS READER parity: every ODS fixture describes to the same
schema in PHP and in Python, key order and int/float included.

The fixtures live in the PHP repo, so both readers see identical bytes.
edge.ods exists for this suite: it holds what LibreOffice rewrites on save, the
constructs where two readers written from one design are most likely to part
ways.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import holy_sheet
from tests import _oracle

pytestmark = pytest.mark.parity


def _empty_cells_as_object(schema: Any) -> Any:
    """PHP encodes an empty associative array as `[]`; make both sides say `{}`."""
    for sheet in schema.get("sheets", []):
        if sheet.get("cells") == []:
            sheet["cells"] = {}
    return schema


@pytest.mark.parametrize("name", ["workbook.ods", "native.ods", "edge.ods", "workbook.xlsx"])
def test_php_and_python_read_the_fixture_identically(php_oracle, name: str) -> None:
    path = _oracle.ods_fixtures_dir() / name

    from_php = _empty_cells_as_object(php_oracle.php_describe(path))
    from_python = holy_sheet.describe(str(path))

    assert json.dumps(from_python, indent=1, ensure_ascii=False) == json.dumps(from_php, indent=1, ensure_ascii=False)
