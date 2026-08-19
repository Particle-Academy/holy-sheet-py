"""PHP's loose numeric + string semantics, written down once.

The whole package leans on this module. `holy-sheet`'s reference engine is PHP,
and roughly every cross-runtime bug this family has shipped lived in the gap
between one language's number handling and another's. The Node port keeps the
same inventory in `src/util.ts`; this is its Python twin, and Python's traps are
DIFFERENT ones:

* ``bool`` is a subclass of ``int``. ``isinstance(True, int)`` is True and
  ``True == 1``, so any branch that tests ``int`` before ``bool`` writes a
  boolean into a numeric cell and never says a word. **Check bool first,
  everywhere.**
* ``float()`` accepts ``"inf"``, ``"nan"``, ``"1_000"`` and surrounding
  whitespace of every kind. PHP's ``is_numeric`` accepts none of those (it does
  accept leading *and*, since PHP 8.0, trailing whitespace). Guard the cast with
  :func:`is_numeric_string`.
* ``round()`` is BANKER'S rounding -- ``round(0.5)`` is 0 and ``round(2.5)`` is
  2. PHP's ``round()`` is half away from zero. Use :func:`php_round`; never the
  builtin.
* ``f"{v:.14f}"`` rounds half to EVEN. PHP's ``number_format`` rounds half away
  from zero. That is the ``<v>`` serialisation of every float cell in every
  sheet, so it is not a corner: use :func:`php_number_format`.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

# PHP's zend_long on every platform this package targets.
PHP_INT_MIN = -(2**63)
PHP_INT_MAX = 2**63 - 1

# PHP's numeric-string grammar (Zend's LNUM / DNUM / EXPONENT_DNUM), including
# the trailing-whitespace allowance PHP 8.0 added. Deliberately NOT Python's:
# "inf", "nan", "0x1A" and "1_000" are all rejected here and all accepted by
# float(). The Node port's regex is the same grammar minus the trailing `\s*`,
# which is a missing anchor rather than a decision -- the parity plan rules for
# PHP, so the anchor is here.
_NUMERIC_STRING = re.compile(
    r"^[ \t\n\r\v\f]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[ \t\n\r\v\f]*$"
)

# An integer-shaped numeric string: no point, no exponent. PHP hands these back
# as `int` from `$s + 0` when they fit in zend_long, and as `float` when they do
# not -- it does NOT clamp, which is what `(int)` would have done.
_INTEGER_STRING = re.compile(r"^[ \t\n\r\v\f]*[+-]?\d+[ \t\n\r\v\f]*$")

_PHP_SPACE = " \t\n\r\v\f"


def is_numeric_string(value: Any) -> bool:
    """PHP ``is_numeric``.

    True for int/float (but not bool, and not NaN/inf -- PHP has no numeric
    string for those) and for strings matching PHP's numeric grammar.
    """
    if isinstance(value, bool):  # before int, always
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if not isinstance(value, str):
        return False
    return _NUMERIC_STRING.match(value) is not None


def numeric_string_to_number(value: str) -> int | float:
    """PHP's ``$value + 0`` on a numeric string.

    Integer-shaped and inside zend_long's range -> ``int``; everything else ->
    ``float``. Both halves matter:

    * The float path is why ``"1e21"`` is 1e21 and not ``PHP_INT_MAX``. PHP's
      ``(int)`` cast CLAMPS, so a dot-test coercion turned every magnitude past
      the int range into 9223372036854775807 -- a number nobody wrote.
    * The int path is why ``"007"`` is the integer 7 and lands in the sheet as
      ``<v>7</v>`` rather than through the float formatter.
    """
    text = value.strip(_PHP_SPACE)
    if _INTEGER_STRING.match(value) is not None:
        as_int = int(text)
        if PHP_INT_MIN <= as_int <= PHP_INT_MAX:
            return as_int
    return float(text)


def php_round(value: float, precision: int = 0) -> float:
    """PHP's ``round()`` -- half AWAY FROM ZERO.

    Python's builtin is half-to-even (``round(0.5) == 0``, ``round(2.5) == 2``),
    which is a different function wearing the same name. Every rounding decision
    in this package goes through here; the builtin is never called.

    At a non-zero `precision` the value is quantised from its SHORTEST
    round-trip decimal representation rather than its exact binary expansion, so
    ``php_round(1.2345, 3)`` is 1.235 and not 1.234. That is PHP's answer, and
    it is the answer a caller writing "1.2345" means -- the exact double is
    1.23449999999999993, and rounding *that* is technically defensible and
    useless. (PHP reaches it through a floating-point pre-rounding step;
    quantising the repr is the same intent stated directly.)

    :func:`php_number_format` deliberately does NOT pre-round, because PHP's own
    ``number_format`` does not either once the scaled value passes 1e15 -- which
    at 14 places is every value from 10 upwards. Two jobs, two rules; the
    inconsistency is the reference engine's and is reproduced rather than tidied.
    """
    if not math.isfinite(value):
        return value
    if precision == 0:
        return float(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))
    with localcontext() as ctx:
        ctx.prec = _decimal_precision_for(value)
        quantised = Decimal(repr(value)).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP
        )
    return float(quantised)


def php_number_format(value: float, decimals: int) -> str:
    """PHP's ``number_format($value, $decimals, '.', '')``.

    The rule, stated rather than emulated: **round the value's exact binary
    expansion at `decimals` places, half away from zero, and suppress the sign
    when the result is zero.**

    Two notes, because each looks like a bug from the other side:

    * ``f"{value:.14f}"`` cannot stand in. It rounds half to even, so a value
      that ties at the 14th decimal -- any ``j / 2**15`` with odd ``j``, the
      only doubles whose decimal expansion terminates in a 5 there -- comes out
      one digit low. ``tests/fixtures.py`` carries two of them for exactly this
      reason, and ``tests/test_numeric.py`` pins the rule directly.
    * PHP's own ``number_format`` reaches this answer through a floating-point
      pre-rounding step whose result CHANGED between PHP 8.3 and 8.4 (verified
      against 8.4.20: it no longer bails out of rounding at ``1e15`` the way the
      older code did, so ``number_format(32.666666666666664, 14)`` disagrees
      with ``sprintf('%.14F', ...)`` of the same double). Reproducing that
      artefact would make this package's output a function of whichever PHP the
      maintainer built against, which is not parity with anything. So the rule
      is implemented and the artefact is not; the two agree everywhere except
      the 15th significant digit of values below 10, and the parity oracle
      proves it part-for-part on every fixture.
    """
    if not math.isfinite(value):
        # PHP's number_format yields 0 for NAN/INF; "NaN" in a cell is a
        # corrupt sheet, not a big number.
        return "0" if decimals <= 0 else "0." + "0" * decimals
    with localcontext() as ctx:
        ctx.prec = _decimal_precision_for(value)
        quantised = Decimal(value).quantize(
            Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
        )
    text = f"{quantised:f}"
    if text.startswith("-") and quantised == 0:
        # number_format(-0.0, 14) is "0.00000000000000" in PHP -- no sign.
        text = text[1:]
    if decimals > 0 and "." not in text:
        text += "." + "0" * decimals
    return text


def _decimal_precision_for(value: float) -> int:
    """Enough significant digits that `Decimal.quantize` never overflows.

    A double's exact expansion runs to ~1080 digits at the denormal end and
    needs 315 before the point at 1e300. The default 28-digit context raises
    InvalidOperation on both, which would surface as a crash on a perfectly
    ordinary large number.
    """
    exponent = 0 if value == 0 else math.floor(math.log10(abs(value)))
    return max(64, abs(exponent) + 80)


def format_float(value: float) -> str:
    """The ``<v>`` serialisation of a float, matching the PHP writer exactly.

    PHP: ``rtrim(rtrim(number_format($v, 14, '.', ''), '0'), '.')``.

    **Only ever trim a FRACTION.** The Node port trimmed unconditionally, and
    because `toFixed` switches to exponential notation at 1e21 the trim chewed
    on the EXPONENT instead: ``(1e300).toFixed(14)`` is ``"1e+300"`` and the
    trailing-zero strip turned that into ``1e+3``. Not a crash, not invalid XML
    -- simply a different number, silently, in a file someone opens later and
    believes. Python's ``format`` never switches to exponential for ``f``, so
    the hazard is absent here; the rule is written down anyway, because the next
    port will be offered a formatter that does.
    """
    text = php_number_format(value, 14)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0", "-") else text


def php_to_string(value: Any) -> str:
    """PHP's ``(string)`` cast, used for cached formula values.

    Bools are the trap: PHP renders true as ``"1"`` and false as the EMPTY
    string, while Python's ``str(True)`` is ``"True"`` -- which would put the
    word "True" in a spreadsheet cell.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # before int, always
        return "1" if value else ""
    if isinstance(value, float):
        return php_float_to_string(value)
    return str(value)


def php_float_to_string(value: float) -> str:
    """PHP's float-to-string at the default ``precision=14`` ini setting.

    ``zend_gcvt(value, 14, '.', 'E')``: %G-style, exponential when the exponent
    is below -5 or at/above the digit count, and the mantissa always carries a
    ``.0`` in exponential form (PHP prints ``1.0E+21``, not ``1E+21``).
    """
    if math.isnan(value):
        return "NAN"
    if math.isinf(value):
        return "INF" if value > 0 else "-INF"
    if value == 0:
        return "0" if math.copysign(1.0, value) > 0 else "-0"

    exponent = math.floor(math.log10(abs(value)))
    if exponent < -5 or exponent >= 14:
        mantissa = f"{value:.13E}"
        digits, exp_text = mantissa.split("E")
        digits = digits.rstrip("0").rstrip(".")
        if "." not in digits:
            digits += ".0"
        return f"{digits}E{exp_text[0]}{int(exp_text[1:])}"

    text = f"{value:.{max(0, 14 - 1 - exponent)}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def type_of(value: Any) -> str:
    """PHP's debug type name, used by the Validator's ``got`` field.

    ``bool`` is checked before ``int`` (Python subclassing) and dict/list are
    split the way PHP's ``array_is_list`` splits an array.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int, always
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def is_array(value: Any) -> bool:
    """PHP ``is_array`` -- one PHP array is both a list and a map."""
    return isinstance(value, (list, tuple, dict))


def is_list(value: Any) -> bool:
    """PHP ``array_is_list``."""
    return isinstance(value, (list, tuple))


def entries(value: Any) -> list[tuple[Any, Any]]:
    """Iterate a PHP-array-shaped value as (key, value) pairs, in order.

    Lists yield integer keys, dicts yield their own keys in insertion order --
    which is what makes the ``cells`` map's document-order contract free in
    Python, where the Node port had to reach for an ordered structure.
    """
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, (list, tuple)):
        return list(enumerate(value))
    return []
