"""Column-type inference for tabular data.

Used by `from_array` / `from_csv` when the caller does not supply a `type`.
Rules favour PREDICTABILITY over cleverness: an agent has to be able to
anticipate what a given input will infer to, or it cannot trust the output.

Inference looks at the header AND a sample of values. The header supplies
semantic intent (a "Revenue" column probably wants currency formatting); the
values veto that intent when the data does not fit.
"""

from __future__ import annotations

import math
import re
from typing import Any

from ..helpers.php import is_numeric_string

_SAMPLE_SIZE = 50

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)

_HEADER_INTEGER = re.compile(
    r"(^|[\s_])(count|qty|quantity|num|number|id|n)([\s_]|$)", re.IGNORECASE
)
_HEADER_CURRENCY = re.compile(
    r"(price|amount|cost|revenue|fee|total|salary|budget|balance)", re.IGNORECASE
)
_HEADER_PERCENT = re.compile(r"(rate|percent|growth|yoy|margin|share|ratio)", re.IGNORECASE)

_INTEGER_TEXT = re.compile(r"^-?\d+$")


class Inference:
    @staticmethod
    def detect(
        column_values: list[Any], header_name: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        options = options or {}
        sample = _non_null_sample(column_values)

        if not sample:
            return {"header": header_name, "type": "auto"}

        if _all_boolean(sample):
            return {"header": header_name, "type": "boolean"}

        all_numeric = _all_numeric(sample)
        all_in_range = all_numeric and _all_in_range_01(sample)
        all_integer = all_numeric and _all_integer(sample)

        if _all_match(sample, _ISO_DATE):
            return {"header": header_name, "type": "date"}
        if _all_match(sample, _ISO_DATETIME):
            return {"header": header_name, "type": "datetime"}

        if all_numeric:
            if _HEADER_PERCENT.search(header_name) and all_in_range:
                return {
                    "header": header_name,
                    "type": "percent",
                    # `?: 1` in PHP -- 0 decimals is falsy there, so a whole
                    # -number percent column still gets one decimal place.
                    "decimals": _detect_decimals(sample) or 1,
                }
            if _HEADER_CURRENCY.search(header_name):
                return {
                    "header": header_name,
                    "type": "currency",
                    "currency": options.get("currency", "USD"),
                    "decimals": _detect_decimals(sample),
                }
            if _HEADER_INTEGER.search(header_name) and all_integer:
                return {"header": header_name, "type": "integer"}
            if all_integer:
                return {"header": header_name, "type": "integer"}

            column: dict[str, Any] = {"header": header_name, "type": "number"}
            decimals = _detect_decimals(sample)
            if decimals is not None and decimals > 0:
                column["decimals"] = decimals
            return column

        if _all_stringish(sample):
            return {"header": header_name, "type": "string"}

        # Mixed -- let the per-cell normalizer decide.
        return {"header": header_name, "type": "auto"}


def _non_null_sample(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    for value in values:
        if value is None:
            continue
        out.append(value)
        if len(out) >= _SAMPLE_SIZE:
            break
    return out


def _all_boolean(sample: list[Any]) -> bool:
    return all(isinstance(value, bool) for value in sample)


def _all_numeric(sample: list[Any]) -> bool:
    for value in sample:
        if isinstance(value, bool):  # before int, always
            return False
        if not isinstance(value, (int, float)) and not (
            isinstance(value, str) and is_numeric_string(value)
        ):
            return False
    return True


def _all_integer(sample: list[Any]) -> bool:
    for value in sample:
        if isinstance(value, bool):  # before int, always
            return False
        if isinstance(value, int):
            continue
        if isinstance(value, float) and math.floor(value) == value:
            continue
        if isinstance(value, str) and _INTEGER_TEXT.match(value):
            continue
        return False
    return True


def _all_in_range_01(sample: list[Any]) -> bool:
    return all(0 <= float(value) <= 1 for value in sample)


def _detect_decimals(sample: list[Any]) -> int | None:
    largest = 0
    saw_float = False
    for value in sample:
        text = value if isinstance(value, str) else _plain(value)
        if "." in text:
            saw_float = True
            places = len(text.split(".", 1)[1].rstrip("0"))
            largest = max(largest, places)
    if not saw_float:
        return 0
    # Never fewer than 2 once a float has been seen -- one decimal place on
    # money reads as a typo.
    return max(largest, 2)


def _plain(value: Any) -> str:
    """The string form used only for counting decimal places."""
    if isinstance(value, bool):  # before int, always
        return "1" if value else ""
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _all_match(sample: list[Any], pattern: re.Pattern[str]) -> bool:
    return all(isinstance(value, str) and pattern.match(value) for value in sample)


def _all_stringish(sample: list[Any]) -> bool:
    return all(isinstance(value, str) for value in sample)
