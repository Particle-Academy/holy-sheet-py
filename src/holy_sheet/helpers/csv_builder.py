"""CSV string OR file path -> a Holy Sheet schema.

    schema = holy_sheet.from_csv("Name,Age\\nAlice,30\\nBob,42")
    schema = holy_sheet.from_csv("/tmp/users.csv")

The first row is always the header row. Parsing goes through the standard
library's `csv` module, so quoting, embedded newlines and commas-in-fields
round-trip correctly with no third-party CSV dependency.

**Accepting a PATH as well as content is a deliberate choice, not an accident.**
The two reference engines disagree here: PHP's `CsvBuilder` sniffs between the
two, Node's takes content only and tells you to read files yourself (it targets
browsers, where there is no filesystem to read). PHP is the reference and the
superset, so this follows PHP -- and the sniff rule is spelled out in
`_resolve_content` so it reads as a decision rather than a guess.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any

from .array_builder import ArrayBuilder
from .php import is_numeric_string, numeric_string_to_number


class CsvBuilder:
    @staticmethod
    def build(csv_or_path: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        content = _resolve_content(csv_or_path)

        delimiter = options.get("delimiter", ",")
        enclosure = options.get("enclosure", '"')
        sheet_name = options.get("sheetName", "Sheet 1")

        rows = _parse_rows(content, delimiter, enclosure)

        if not rows:
            return {"sheets": [{"name": sheet_name, "columns": [], "rows": []}]}

        headers = [str(value) for value in rows[0]]
        data_rows = [list(row) for row in rows[1:]]

        # Coerce numeric strings to native numbers so type inference sees raw
        # values rather than text. PHP's own numeric-string juggling, NOT a dot
        # test: `(int)` clamps at PHP_INT_MAX so "1e21" became
        # 9223372036854775807, and a dot test sent "2e-3" down the int branch
        # where it became 0.
        for row in data_rows:
            for i, cell in enumerate(row):
                if isinstance(cell, str) and is_numeric_string(cell):
                    row[i] = numeric_string_to_number(cell)

        return ArrayBuilder.build(data_rows, headers, sheet_name, options)


def _resolve_content(csv_or_path: str) -> str:
    """Path or content?

    Treated as a PATH only when all three hold: no newline anywhere, under 4096
    characters, and a readable file exists at that name. Anything else is
    content. The three conditions together are what stop a one-line CSV like
    "a,b" from being probed as a filename on every call.
    """
    looks_like_path = "\n" not in csv_or_path and len(csv_or_path) < 4096
    if looks_like_path:
        try:
            if os.path.isfile(csv_or_path) and os.access(csv_or_path, os.R_OK):
                with open(csv_or_path, "r", encoding="utf-8", newline="") as handle:
                    return handle.read()
        except OSError as error:  # pragma: no cover - filesystem-dependent
            raise RuntimeError(f"[holy-sheet] failed to read CSV at {csv_or_path}") from error
    return csv_or_path


def _parse_rows(content: str, delimiter: str, enclosure: str) -> list[list[str]]:
    reader = csv.reader(
        io.StringIO(content, newline=""), delimiter=delimiter, quotechar=enclosure
    )
    # Blank lines produce an empty row; skip them, as `fgetcsv` returning
    # `[null]` is skipped on the PHP side.
    return [row for row in reader if row and row != [""]]
