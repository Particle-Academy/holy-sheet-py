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
* **`json_encode` fails on NaN, INF, invalid UTF-8 and nesting past its depth**,
  and since holy-sheet 2.3.2 `SheetDiff::canon()` throws `JsonException` there
  (2.3.1 cast the `false` to `""`, so any two such values compared as the same).
  :func:`canon` raises `ValueError` for the same inputs, a lone surrogate
  standing in for invalid UTF-8, at the same depth: 4096 arrays, an empty one
  counting as a level. It walks an explicit stack, because Python's default
  recursion limit (1000) would otherwise decide instead of PHP's depth.

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

#: The depth `SheetDiff::canon()` passes `json_encode` (PHP 2.3.2).
_JSON_MAX_DEPTH = 4096

# `ctype_digit` in PHP's C locale: one or more ASCII digits. Not `str.isdigit()`,
# which accepts every Unicode digit.
_DIGITS = re.compile(r"[0-9]+", re.ASCII)

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


def php_integer(value: Any) -> int | None:
    """PHP 2.3.3 `SheetReducer::integer()`: an int, or a string of digits; else None.

    `True` is not an int (`is_int(true)` is false), nor is `2.0`, nor an integer
    outside PHP's range, which `json_decode` would have made a float. A digit
    string past the range saturates, as `(int)` does.
    """
    if isinstance(value, bool):  # before int, always
        return None
    if isinstance(value, int):
        return value if PHP_INT_MIN <= value <= PHP_INT_MAX else None
    if isinstance(value, str) and _DIGITS.fullmatch(value):
        return php_int_cast(value)
    return None


def is_index_key(key: Any) -> bool:
    """Whether a PHP array key (as :func:`php_key` stores it) reads as a column
    index: an int key, or a string of digits (`ctype_digit`)."""
    if isinstance(key, bool):
        return False
    return isinstance(key, int) or (isinstance(key, str) and _DIGITS.fullmatch(key) is not None)


def php_trim(value: str) -> str:
    """PHP `trim()` with its default set: space, tab, LF, CR, NUL, vertical tab."""
    return value.strip(_PHP_TRIM)


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
    match = _A1.fullmatch(ascii_upper(php_trim(address)))
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


def canon(value: Any) -> str:
    """`SheetDiff::canon()`: JSON with map keys sorted and list order kept.

    Only equality between two canon strings is ever used, so what matters is
    that this relation is PHP's: `1` and `1.0` differ, `true` and `1` differ
    (Python's `True == 1` never reaches the comparison, because the JSON text
    is compared, and `true` is not `1`), `[]` and `{}` are the same, and
    `{"0": x}` is `[x]`.

    Raises `ValueError` where PHP 2.3.2 throws `JsonException`: NaN or an
    infinity, an int too large for a float (`json_decode`'s INF), a lone
    surrogate in a string or a key (invalid UTF-8 in PHP), and more than 4096
    nested arrays. The walk is a loop over an explicit stack, not recursion, so
    that limit is PHP's and not Python's recursion limit.
    """
    if not is_array(value):
        return _canon_scalar(value)

    stack: list[_Frame] = [_Frame(value, None, 1)]

    while True:
        frame = stack[-1]

        if frame.position < len(frame.pairs):
            key, item = frame.pairs[frame.position]
            frame.position += 1
            if is_array(item):
                stack.append(_Frame(item, key, len(stack) + 1))
            else:
                frame.parts.append(frame.member(key, _canon_scalar(item)))
            continue

        stack.pop()
        text = "[" + ",".join(frame.parts) + "]" if frame.is_list else "{" + ",".join(frame.parts) + "}"
        if not stack:
            return text
        stack[-1].parts.append(stack[-1].member(frame.key, text))


class _Frame:
    """One array of :func:`canon`'s walk: its pairs in output order, and the text so far."""

    __slots__ = ("pairs", "key", "position", "parts", "is_list")

    def __init__(self, value: Any, key: Any, depth: int) -> None:
        if depth > _JSON_MAX_DEPTH:
            raise ValueError("[holy-sheet] cannot compare a value JSON cannot hold: Maximum stack depth exceeded")
        pairs = php_pairs(value)
        if not is_php_list(pairs):
            # ksort($value, SORT_STRING). Code-point order is UTF-8 byte order.
            pairs.sort(key=lambda pair: str(pair[0]))
        self.pairs = pairs
        self.key = key
        self.position = 0
        self.parts: list[str] = []
        # json_encode decides list-or-object AFTER the sort: {"1": b, "0": a} is [a, b].
        self.is_list = is_php_list(pairs)

    def member(self, key: Any, text: str) -> str:
        return text if self.is_list else _canon_string(str(key)) + ":" + text


def _canon_scalar(value: Any) -> str:
    if value is None or isinstance(value, bool):  # bool before int, always
        return json.dumps(value)
    if isinstance(value, int):
        if PHP_INT_MIN <= value <= PHP_INT_MAX:
            return json.dumps(value)
        try:
            value = float(value)
        except OverflowError as error:
            raise ValueError("[holy-sheet] cannot compare a value JSON cannot hold: Inf and NaN cannot be JSON encoded") from error
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("[holy-sheet] cannot compare a value JSON cannot hold: Inf and NaN cannot be JSON encoded")
        return json.dumps(value)
    if isinstance(value, str):
        return _canon_string(value)
    raise TypeError(f"[holy-sheet] {type(value).__name__} is not a schema value")


def _canon_string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "[holy-sheet] cannot compare a value JSON cannot hold: Malformed UTF-8 characters, possibly incorrectly encoded"
        ) from error
    return json.dumps(value, ensure_ascii=False)


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
