"""The Agent surface -- Holy Sheet's structured-tool API.

Built for LLM tool-use: validate-then-write semantics, a structured error
format, a JSON Schema export for tool definitions, and round-trip
introspection. Everything here is a module-level function, which is the Python
shape of PHP's all-static `Agent` class and TS's `Agent` object literal -- no DI
container, no construction ceremony, one import.

Applications that want injection should reach for the underlying services
directly (`Validator`, `Normalizer`, `XlsxWriter`), exactly as the peers advise.

Naming: the peers' camelCase becomes snake_case (`toBytes` -> `to_bytes`), which
is the only thing about this surface that differs between the three runtimes.
"""

from __future__ import annotations

import json
import os
from importlib import resources
from typing import Any

from .helpers.array_builder import ArrayBuilder
from .helpers.csv_builder import CsvBuilder
from .reader.xlsx_reader import XlsxReader
from .schema.formula_linter import FormulaLinter
from .schema.normalizer import Normalizer
from .schema.validator import Validator
from .writer.xlsx_writer import XlsxWriter

#: This package's own version. It moves on its own schedule, like every package
#: in the suite; the FEATURE baseline it was ported from is PHP holy-sheet 1.3.0.
def _installed_version() -> str:
    """This package's version, read from the INSTALLED distribution metadata.

    Not a literal. A literal here is a second copy of a number that already
    lives in ``pyproject.toml``, and the two drift with nothing comparing them.
    That is not hypothetical in this estate: ``fancy-flow-py`` shipped
    ``__version__ = "0.1.0"`` against a 0.4.0 distribution for three releases,
    and the runtime's first outside consumer installed 0.4.0, read 0.1.0, and
    reported it.

    Reading from metadata removes the second copy rather than re-syncing it, so
    there is nothing left to drift. The fallback covers a source tree that was
    never installed — a case where ``pyproject.toml`` is the only truth and no
    distribution exists to disagree with it.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _distribution_version

    try:
        return _distribution_version("fancy-holy-sheet")
    except PackageNotFoundError:  # pragma: no cover — an uninstalled source tree
        return "0.0.0+unknown"


VERSION = _installed_version()

#: The PHP release whose behaviour this port reproduces. Recorded because the
#: three engines version independently and "which holy-sheet is this?" is
#: otherwise unanswerable from the package alone.
FEATURE_BASELINE = "1.3.0"

_SCHEMA_FILENAME = "holy_sheet.schema.json"


def validate(schema: Any) -> list[dict[str, Any]]:
    """Validate without writing anything. Empty list means valid."""
    return Validator().validate(schema)


def validate_and_repair(schema: Any) -> dict[str, Any]:
    """Validate and apply conservative repairs in one call.

    Returns `{schema, errors, repairs}` -- the repaired schema, the errors that
    remain after repair, and human-readable notes on what was changed. The notes
    matter: an agent that logs them stops emitting the same malformed shape,
    which auto-repair alone would let it do forever.
    """
    return Validator().validate_and_repair(schema)


def to_bytes(schema: Any) -> bytes:
    """The xlsx bytes, without touching disk.

    The universal entry point -- an HTTP response body, a blob, an attachment.
    Raises `SchemaException` when the schema is invalid; call `validate()` first
    to dry-run instead.
    """
    Validator().assert_valid(schema)
    workbook = Normalizer().normalize(schema)
    return XlsxWriter().to_bytes(workbook)


def write(schema: Any, path: str) -> dict[str, Any]:
    """Write a workbook to disk. Synchronous.

    Deliberately NOT async. PHP's is synchronous; Node's is async only because
    browsers have no synchronous filesystem, which is not a constraint Python
    shares. Returns `{path, bytes, sheets}`.
    """
    Validator().assert_valid(schema)
    workbook = Normalizer().normalize(schema)
    XlsxWriter().write(workbook, path)
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "sheets": len(workbook.sheets),
    }


def read(data: bytes) -> dict[str, Any]:
    """Round-trip xlsx BYTES back to a Holy Sheet schema."""
    return XlsxReader().describe(data)


def describe(path: str) -> dict[str, Any]:
    """Round-trip an xlsx FILE back to a Holy Sheet schema.

    Returns `{"error": "not_found", "path": ...}` for a missing path rather than
    raising -- an agent tool call that returns a structured miss is recoverable,
    where an exception is a dead end.
    """
    if not os.path.isfile(path):
        return {"error": "not_found", "path": path}
    with open(path, "rb") as handle:
        return XlsxReader().describe(handle.read())


def tool_definition() -> dict[str, Any]:
    """The JSON Schema describing the input format.

    Drop it into an Anthropic `tool_use` block, an OpenAI function definition,
    or anything else that consumes JSON Schema.

    **This file is byte-identical across all three engines by hand**, and each
    repo pins its SHA-256 (see `tests/test_schema_sync.py`) so a one-sided edit
    fails a build instead of quietly telling an LLM that one backend's API is
    different from the others'.

    A missing file RAISES rather than returning `{}`. PHP's version returns an
    empty array, which is the worse failure: an empty tool definition handed to
    a model produces no error anywhere, just a model with no idea what the tool
    accepts.
    """
    try:
        text = resources.files(__package__).joinpath(_SCHEMA_FILENAME).read_text("utf-8")
    except (FileNotFoundError, OSError) as error:
        raise RuntimeError(
            f"[holy-sheet] {_SCHEMA_FILENAME} is missing from the installed package. "
            "It is the tool definition handed to an LLM; returning an empty one would "
            "silently strip every hint the model has."
        ) from error
    return json.loads(text)


def from_array(
    rows: list[list[Any]],
    headers: list[str] | None = None,
    sheet_name: str = "Sheet 1",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a schema from a flat list of rows, with inferred column types.

    `headers` may be omitted, in which case the first row is used as the header
    row.
    """
    return ArrayBuilder.build(rows, headers, sheet_name, options or {})


def from_csv(csv_or_path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a schema from CSV content OR a filesystem path.

    Accepting both is PHP's behaviour and is the superset; Node takes content
    only because it targets browsers. See `CsvBuilder._resolve_content` for the
    sniff rule.
    """
    return CsvBuilder.build(csv_or_path, options or {})


def lint(schema: Any) -> list[dict[str, str]]:
    """Evaluate every formula and report the cells that produce Excel errors.

    Catches what an LLM actually gets wrong: the header-row off-by-one, a string
    in arithmetic, a reference to a cell that does not exist, a circular
    dependency. Empty list means every formula evaluates cleanly.
    """
    return FormulaLinter().lint(schema)


def version() -> str:
    """This package's version."""
    return VERSION
