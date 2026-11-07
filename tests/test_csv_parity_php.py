"""CC-fancy-0012: `to_csv` against PHP's `Exchange\\Csv::toCsv()`, call for call.

The text and the report must be identical. The inputs live in the PHP repo
(tests/fixtures/csv/tables.json) so the Node port runs the same ones. PHP's
output is compared as parsed JSON, which keeps its int / float distinction
(the script encodes with JSON_PRESERVE_ZERO_FRACTION).
"""

from __future__ import annotations

from typing import Any

import pytest

from holy_sheet import OptionsInvalid, to_csv
from tests import _oracle

CALLS = _oracle.csv_tables()


def _ours(call: dict[str, Any]) -> dict[str, Any]:
    try:
        return to_csv(call["table"], call["options"])
    except OptionsInvalid as e:
        return {"error": "OptionsInvalid", "message": str(e)}


@pytest.fixture(scope="module")
def php_results(php_oracle) -> list[dict[str, Any]]:
    return php_oracle.php_csv([{"table": c["table"], "options": c["options"]} for c in CALLS])


@pytest.mark.parity
def test_compared_every_call(php_results) -> None:
    assert len(CALLS) > 12
    assert len(php_results) == len(CALLS)


@pytest.mark.parity
@pytest.mark.parametrize("index", range(len(CALLS)), ids=[c["name"] for c in CALLS])
def test_matches_php(php_results, index: int) -> None:
    assert _ours(CALLS[index]) == php_results[index]


# Pinned bytes, so the writer is tested even where PHP is not installed.


def test_quotes_rfc_4180_and_ends_every_line_crlf() -> None:
    out = to_csv({"rows": [{"cells": [{"t": "string", "v": 'a,"b"'}, {"t": "number", "v": 1.5}]}, {"cells": [{"t": "bool", "v": True}, {"t": "empty"}]}]})
    assert out["text"] == '"a,""b""",1.5\r\nTRUE,\r\n'


def test_escapes_a_leading_formula_character_by_default() -> None:
    out = to_csv({"rows": [{"cells": [{"t": "string", "v": "=SUM(A1)"}, {"t": "number", "v": -1}]}]})
    assert out["text"] == "'=SUM(A1),-1\r\n"
    assert out["report"]["approximated"] == [{"kind": "csv-escaped", "count": 1, "where": ["/rows/0/cells/0"]}]


def test_leaves_it_alone_only_when_the_host_says_so() -> None:
    assert to_csv({"rows": [{"cells": [{"t": "string", "v": "=1"}]}]}, {"escapeFormulas": False})["text"] == "=1\r\n"


def test_a_bool_is_not_a_number() -> None:
    # bool is an int in Python; PHP's is_int(true) is false, so it is canonical 0.
    assert to_csv({"rows": [{"cells": [{"t": "number", "v": True}]}]})["text"] == "0\r\n"


def test_refuses_an_option_it_does_not_know() -> None:
    with pytest.raises(OptionsInvalid):
        to_csv({"rows": []}, {"delimiter": ";"})
