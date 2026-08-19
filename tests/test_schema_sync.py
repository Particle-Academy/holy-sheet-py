"""The tool definition is one contract in three repos, kept in sync BY HAND.

`holy-sheet/skills/holy-sheet.schema.json`,
`holy-sheet-js/src/holy-sheet.schema.json` and this package's
`holy_sheet/holy_sheet.schema.json` are byte-identical copies maintained by
remembering to edit all three. This is the file handed to an LLM as the tool
definition, so a one-sided edit does not fail a build -- it changes what an
agent is told the API is on one backend and not the others, which is the kind of
bug that surfaces as "the model keeps sending the wrong shape" weeks later.

## How to change the schema

Edit all three copies, run any of the suites, and paste the new hash into all
three tests. The mild annoyance IS the mechanism: update one side and the other
repos go red on their next push. The alternative is silence.

The peers are `holy-sheet/tests/Unit/SchemaSyncTest.php` and
`holy-sheet-js/tests/schema-sync.test.ts`, both pinning this same constant.

## Why the content is normalised before hashing

No repo in the trio has a `.gitattributes`, so the checkout decides line
endings: the file is stored LF in git and lands CRLF on a Windows working tree.
A checksum over the RAW bytes passes on Linux CI and fails on the maintainer's
own machine for a reason that has nothing to do with the copies being out of
sync -- a worse failure than the one being prevented, because it teaches people
to distrust the test. (On a CRLF checkout the raw hash is
`6e08d01e9026…`; that number is an artefact of the checkout, not the contract.)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import holy_sheet

SHARED_SCHEMA_SHA256 = "a09d491ee67c32d9c37e8608a6bc3334993a6c77f978e5ffe158f769e77e2d9c"

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "holy_sheet" / "holy_sheet.schema.json"
)


def _normalised_sha256(raw: bytes) -> str:
    """Hash the content, independent of how the checkout wrote the newlines."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def test_the_shipped_schema_matches_the_shared_checksum() -> None:
    assert _normalised_sha256(SCHEMA_PATH.read_bytes()) == SHARED_SCHEMA_SHA256


def test_the_schema_is_valid_json_with_the_fields_a_tool_definition_needs() -> None:
    """A checksum alone would happily pin a corrupt file."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert isinstance(schema, dict)
    assert isinstance(schema.get("$schema"), str)
    assert schema.get("type") == "object"
    assert isinstance(schema.get("properties"), dict)


def test_tool_definition_returns_the_parsed_schema() -> None:
    definition = holy_sheet.tool_definition()

    assert definition["title"] == "Holy Sheet workbook schema"
    assert set(definition["definitions"]) >= {"Sheet", "Column", "CellData", "CellFormat"}


def test_tool_definition_never_degrades_to_an_empty_dict(monkeypatch) -> None:
    """A missing file must raise, not return {}.

    PHP's version returns an empty array when the file is absent. That is the
    worse failure: an empty tool definition produces no error anywhere, just a
    model that has been told nothing about the tool it is holding.
    """
    import holy_sheet.agent as agent

    monkeypatch.setattr(agent, "_SCHEMA_FILENAME", "definitely-not-here.json")
    try:
        agent.tool_definition()
    except RuntimeError as error:
        assert "missing from the installed package" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a missing schema file must raise")
