"""PHP array semantics for the op engine, written down once.

`Ops\\SheetReducer` and `Ops\\SheetDiff` in the PHP reference run on PHP arrays,
and a PHP array is not a Python dict. Four differences decide what the ops do,
and every one of them is reproduced here rather than approximated:

* **One array is both a list and a map.** `[120, 140]` and `{"0": 120, "1": 140}`
  are the SAME value in PHP (`json_decode` turns a numeric string key into an
  int key), and so are `[]` and `{}`. :func:`php_pairs` normalises keys the way
  PHP does, and :func:`canon` encodes a map whose keys are 0..n-1 in order as a
  list, exactly as `json_encode` does.
* **`(int)` is not `int()`.** `(int) "12abc"` is 12, `(int) "1e3"` is 1000,
  `(int) 1e20` wraps modulo 2**64, `(int) "99999999999999999999"` saturates.
  :func:`php_int_cast` mirrors PHP 8.4, and the cases above are pinned by tests
  against values PHP 8.4 printed.
* **`===` is typed.** `1 === 1.0` and `true === 1` are false. Python's `==`
  says both are true, because `bool` subclasses `int`. :func:`identical` checks
  bool before int, every time.
* **`json_encode` fails on NaN, INF, invalid UTF-8 and nesting past 512**, and
  `SheetDiff::canon()` casts that `false` to `""`. So in PHP any two values that
  cannot be encoded compare as the same. :func:`canon` returns `""` for the same
  inputs (a lone surrogate standing in for invalid UTF-8). That is a questionable
  PHP behaviour, mirrored on purpose: it is listed in the 0.3.0 port notes.

Where PHP's integers overflow into floats (row numbers or shift counts past
2**63), this port does not follow: Python's integers do not overflow, and no
schema that validates reaches that range.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..helpers.php import PHP_INT_MAX, PHP_INT_MIN, php_float_to_string
from ..workbook.cell_address import CellAddress

#: `json_encode`'s default depth.
_JSON_MAX_DEPTH = 512

# A string PHP turns into an integer array key: canonical decimal, no "+", no
# leading zero, no "-0". Range-checked separately.
_INT_KEY = re.compile(r"(?:0|-?[1-9][0-9]*)", re.ASCII)

# The numeric prefix `(int) $string` reads (is_numeric_string with errors allowed).
_NUMERIC_PREFIX = re.compile(
    r"[ \t\n\r\v\f]*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)", re.ASCII
)

# PHP `trim()`'s default character list. Not Python's `str.strip()`, which also
# strips NBSP and every other Unicode space and does not strip NUL.
_PHP_TRIM = " \t\n\r\0\x0b"

_A1 = re.compile(r"([A-Z]+)([0-9]+)", re.ASCII)

_ASCII_UPPER = {code: code - 32 for code in range(ord("a"), ord("z") + 1)}

_TWO_POW_63 = 2.0**63
_TWO_POW_64 = 2.0**64


def is_array(value: Any) -> bool:
    """PHP `is_array`: a list and a map are both arrays."""
    return isinstance(value, (list, tuple, dict))


def php_key(key: Any) -> Any:
    """The key PHP stores for `$array[$key]`."""
    if isinstance(key, bool):  # before int, always
        return int(key)
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        if _INT_KEY.fullmatch(key):
            number = int(key)
            if PHP_INT_MIN <= number <= PHP_INT_MAX:
                return number
        return key
    if key is None:
        return ""
    if isinstance(key, float):
        return php_int_cast(key)
    return key


def php_pairs(value: Any) -> list[tuple[Any, Any]]:
    """`foreach ($array as $key => $value)`, keys normalised as PHP stores them.

    Two Python keys PHP would store as one (`"1"` and `1`) collapse the way a
    second assignment does in PHP: the value is replaced, the position kept.
    """
    if isinstance(value, (list, tuple)):
        return list(enumerate(value))
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[php_key(key)] = item
        return list(out.items())
    return []


def is_php_list(pairs: list[tuple[Any, Any]]) -> bool:
    """`array_is_list`: keys are exactly 0..n-1, in order."""
    return all(
        isinstance(key, int) and not isinstance(key, bool) and key == position
        for position, (key, _) in enumerate(pairs)
    )


def values(value: Any) -> list[Any]:
    """`array_values`."""
    return [item for _, item in php_pairs(value)]


def get(array: Any, key: str, default: Any = None) -> Any:
    """`$array[$key] ?? $default` for a string key that is not numeric.

    Null when the container is not an array (PHP's `??` swallows the offset
    access on a scalar), when the key is absent, and when the value IS null.
    """
    if isinstance(array, dict):
        found = array.get(key)
        return default if found is None else found
    return default


def has(array: Any, key: str) -> bool:
    """`array_key_exists($key, $array)` for a string key that is not numeric."""
    return isinstance(array, dict) and key in array


def writable(array: Any) -> dict[Any, Any]:
    """A fresh map to assign string keys into, as PHP would on the same array."""
    if isinstance(array, dict):
        return dict(array)
    if isinstance(array, (list, tuple)):
        return dict(enumerate(array))
    return {}


def php_int_cast(value: Any) -> int:
    """PHP 8.4 `(int) $value`."""
    if value is None:
        return 0
    if isinstance(value, bool):  # before int, always
        return int(value)
    if isinstance(value, int):
        if PHP_INT_MIN <= value <= PHP_INT_MAX:
            return value
        # Out of zend_long range PHP never held an int: json_decode gave a float.
        try:
            value = float(value)
        except OverflowError:
            return 0  # json_decode's INF; (int) INF is 0
    if isinstance(value, float):
        return _float_to_int(value)
    if isinstance(value, str):
        return _string_to_int(value)
    if isinstance(value, (list, tuple, dict)):
        return 1 if value else 0
    raise TypeError(f"[holy-sheet] cannot cast {type(value).__name__} to int")


def _float_to_int(value: float) -> int:
    """`zend_dval_to_lval`: truncate in range, wrap modulo 2**64 outside it."""
    if not math.isfinite(value):
        return 0
    if -_TWO_POW_63 <= value < _TWO_POW_63:
        return int(value)
    remainder = math.fmod(value, _TWO_POW_64)
    if remainder < 0:
        remainder += _TWO_POW_64
    if remainder >= _TWO_POW_63:
        remainder -= _TWO_POW_64
    return int(remainder)


def _string_to_int(value: str) -> int:
    """`(int) $string`: the leading numeric prefix, saturating (not wrapping)."""
    match = _NUMERIC_PREFIX.match(value)
    if match is None:
        return 0
    text = match.group(1)
    if "." not in text and "e" not in text and "E" not in text:
        number = int(text)
        if number > PHP_INT_MAX:
            return PHP_INT_MAX
        if number < PHP_INT_MIN:
            return PHP_INT_MIN
        return number
    parsed = float(text)
    if not math.isfinite(parsed):
        return 0
    if parsed >= _TWO_POW_63:
        return PHP_INT_MAX
    if parsed < -_TWO_POW_63:
        return PHP_INT_MIN
    return int(parsed)


def php_string_cast(value: Any) -> str:
    """PHP `(string) $value`. An array is `"Array"`, as PHP says with a warning."""
    if value is None:
        return ""
    if isinstance(value, bool):  # before int, always
        return "1" if value else ""
    if isinstance(value, int):
        if PHP_INT_MIN <= value <= PHP_INT_MAX:
            return str(value)
        return php_float_to_string(float(value))
    if isinstance(value, float):
        return php_float_to_string(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, dict)):
        return "Array"
    raise TypeError(f"[holy-sheet] cannot cast {type(value).__name__} to string")


def ascii_upper(value: str) -> str:
    """PHP 8.2+ `strtoupper`: ASCII only. `"ß".upper()` is `"SS"`; PHP leaves it."""
    return value.translate(_ASCII_UPPER)


def parse_address(address: str) -> tuple[int, int] | None:
    """PHP `CellAddress::parse`: (column index, 1-based row), or None.

    Not this package's `CellAddress.parse`, which trims with `str.strip()`,
    upper-cases with `str.upper()` and matches `\\d` without `re.ASCII`, so it
    accepts `"ﬀ1"` and Arabic-Indic digits where PHP does not. The ops follow
    PHP exactly; the writer's parser is left as it is.
    """
    match = _A1.fullmatch(ascii_upper(address.strip(_PHP_TRIM)))
    if match is None:
        return None
    return CellAddress.index(match.group(1)), php_int_cast(match.group(2))


def identical(a: Any, b: Any) -> bool:
    """PHP `===`."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):  # before int, always
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_array(a) or is_array(b):
        if not (is_array(a) and is_array(b)):
            return False
        left, right = php_pairs(a), php_pairs(b)
        return len(left) == len(right) and all(
            type(ka) is type(kb) and ka == kb and identical(va, vb)
            for (ka, va), (kb, vb) in zip(left, right)
        )
    a, b = _php_scalar(a), _php_scalar(b)
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, float) and isinstance(b, float) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return a is b


def _php_scalar(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool) and not PHP_INT_MIN <= value <= PHP_INT_MAX:
        try:
            return float(value)
        except OverflowError:
            return math.inf
    return value


class _Unencodable(Exception):
    """`json_encode` would have returned false."""


def canon(value: Any) -> str:
    """`SheetDiff::canon()`: JSON with map keys sorted and list order kept.

    Only equality between two canon strings is ever used, so what matters is
    that this relation is PHP's: `1` and `1.0` differ, `true` and `1` differ
    (Python's `True == 1` never reaches the comparison, because the JSON text
    is compared, and `true` is not `1`), `[]` and `{}` are the same, and
    `{"0": x}` is `[x]`.
    """
    try:
        normalised = _sort_keys(value, 0)
    except _Unencodable:
        return ""
    return json.dumps(normalised, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sort_keys(value: Any, depth: int) -> Any:
    if value is None or isinstance(value, bool):  # bool before int, always
        return value
    if isinstance(value, int):
        if PHP_INT_MIN <= value <= PHP_INT_MAX:
            return value
        try:
            return float(value)
        except OverflowError as error:
            raise _Unencodable from error
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Unencodable
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise _Unencodable from error
        return value
    if is_array(value):
        if depth + 1 > _JSON_MAX_DEPTH:
            raise _Unencodable
        pairs = php_pairs(value)
        if not is_php_list(pairs):
            # ksort($value, SORT_STRING). Code-point order is UTF-8 byte order.
            pairs.sort(key=lambda pair: str(pair[0]))
        children = [(key, _sort_keys(item, depth + 1)) for key, item in pairs]
        # json_encode decides list-or-object AFTER the sort: {"1": b, "0": a} is [a, b].
        if is_php_list(children):
            return [item for _, item in children]
        return {str(key): item for key, item in children}
    raise TypeError(f"[holy-sheet] {type(value).__name__} is not a schema value")


def php_json_view(value: Any) -> Any:
    """What `json_encode` would print for `value`, key order kept, as Python data.

    For comparing this port's output with PHP's: PHP prints `columnWidths`
    `[0 => 120, 1 => 140]` as the list `[120, 140]`, and an empty map as `[]`.
    """
    if is_array(value):
        pairs = php_pairs(value)
        if is_php_list(pairs):
            return [php_json_view(item) for _, item in pairs]
        return {str(key): php_json_view(item) for key, item in pairs}
    return _php_scalar(value)
