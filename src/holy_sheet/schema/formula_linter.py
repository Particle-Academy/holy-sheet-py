"""Evaluate every formula in a schema and report the ones that break.

The linter exists to catch the four bugs an LLM actually introduces with
formulas: referencing the header row instead of the first data row, passing a
string to a numeric operator, citing a cell that does not exist, and building a
circular dependency. Catching those BEFORE the xlsx is written lets an agent
self-correct on the next loop iteration instead of shipping a file whose numbers
are `#VALUE!`.

Supported vocabulary -- the subset agents actually emit:

* operators ``+ - * / ^ & = < > <= >= <>`` and postfix ``%``
* cell refs ``A1``, ``$A$1``, ``Sheet2!B5``; ranges ``A1:A10``
* numeric / string literals, parenthesised expressions
* functions SUM, AVERAGE/AVG, COUNT, COUNTA, MIN, MAX, IF, ROUND, ABS, LEN,
  UPPER, LOWER, CONCAT/CONCATENATE

Out of scope: array formulas, dynamic arrays, structured table refs, named
ranges, and the rest of Excel's 400+ functions. Those return ``#NAME?`` so the
agent learns to avoid them rather than shipping something that silently differs.

Error labels and hint texts are byte-identical to PHP ``Schema\\FormulaLinter``
and the Node port.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import is_numeric_string, php_round, php_to_string
from ..workbook.cell import Cell
from ..workbook.cell_address import CellAddress
from ..workbook.workbook import Workbook
from .normalizer import Normalizer

ERR_VALUE = "#VALUE!"
ERR_REF = "#REF!"
ERR_NAME = "#NAME?"
ERR_DIV0 = "#DIV/0!"
ERR_CIRC = "#CIRC!"

# The functions _call_function() evaluates. Anything else is #NAME?.
_FUNCTIONS = frozenset(
    {
        "SUM", "AVERAGE", "AVG", "COUNT", "COUNTA", "MIN", "MAX", "IF", "ROUND",
        "ABS", "LEN", "UPPER", "LOWER", "CONCAT", "CONCATENATE", "TRUE", "FALSE",
    }
)

_CLEAN_REF = re.compile(r"^[A-Z]+\d+$")
# A sheet qualifier, quoted ('My Sheet'!) or bare (Sheet2!). Group 1 is the name
# as written, quotes included.
_SHEET_QUALIFIER = r"('(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!"
_SHEET_REFS = re.compile(_SHEET_QUALIFIER)
_HINT_REFS = re.compile(r"(?:" + _SHEET_QUALIFIER + r")?\$?([A-Z]+)\$?(\d+)", re.IGNORECASE)

# PHP's ctype_* classes, which are ASCII-only. str.isspace / isdigit / isalpha
# are Unicode-aware: they accepted a no-break space, "é" and "²", which PHP
# rejects (and "²" then crashed float()).
_SPACE = frozenset(" \t\n\r\v\f")
_DIGIT = frozenset("0123456789")
_ALPHA = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_ALNUM = _ALPHA | _DIGIT


class _LinterError(Exception):
    """Short-circuit for an Excel error raised deep inside the parser.

    ``cause`` says why a #NAME? arose in THIS formula: ``"syntax"`` or
    ``"function"``; None for an error that was computed, or inherited from
    another cell. ``detail`` is the syntax fault, or the unknown function's name.
    """

    def __init__(self, error_code: str, cause: str | None = None, detail: str = "") -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.cause = cause
        self.detail = detail

    @classmethod
    def syntax(cls, detail: str) -> _LinterError:
        return cls(ERR_NAME, "syntax", detail)


class FormulaLinter:
    def __init__(self) -> None:
        # Every sheet name, keyed by its lower-cased form. Excel matches sheet
        # names without regard to case, and a reference to a sheet that is not
        # here is #REF! rather than a range of blanks.
        self._sheet_names: dict[str, str] = {}
        # Why a cell's own formula produced #NAME?, keyed `Sheet!A1`: a syntax
        # error, an unknown function, or a linter fault. A cell with #NAME? and
        # no entry here inherited it from a cell it references.
        self._name_causes: dict[str, tuple[str, str]] = {}
        # Greater than 0 while parsing an IF branch that is not taken. The
        # parser evaluates as it goes, so a skipped branch is still PARSED (its
        # syntax errors and unknown functions are reported) but nothing in it
        # is EVALUATED: no operator runs, no cell is read, every value is None.
        self._skipping = 0

    def lint(self, schema: Any) -> list[dict[str, str]]:
        workbook = Normalizer().normalize(schema)
        self._sheet_names = {sheet.name.lower(): sheet.name for sheet in workbook.sheets}
        self._name_causes = {}
        self._skipping = 0
        index = self._build_index(workbook)
        cache: dict[str, Any] = {}
        issues: list[dict[str, str]] = []

        for sheet in workbook.sheets:
            for address, cell in sheet.cells.items():
                if cell.formula is None:
                    continue
                key = f"{sheet.name}!{address}"
                result = self._evaluate(cell.formula, sheet.name, index, cache, [key])
                cache[key] = result

                if _is_error(result):
                    issues.append(
                        {
                            "sheet": sheet.name,
                            "address": address,
                            "formula": cell.formula,
                            "error": result,
                            "hint": self._hint(result, key, cell.formula, sheet.name, index, cache),
                        }
                    )
        return issues

    def _build_index(self, workbook: Workbook) -> dict[str, Cell]:
        index: dict[str, Cell] = {}
        for sheet in workbook.sheets:
            for address, cell in sheet.cells.items():
                index[f"{sheet.name}!{address}"] = cell
        return index

    def _evaluate(
        self,
        formula: str,
        default_sheet: str,
        index: dict[str, Cell],
        cache: dict[str, Any],
        stack: list[str],
    ) -> Any:
        key = stack[-1]
        self._name_causes.pop(key, None)
        try:
            tokens = self._tokenize(formula)
            state = _Cursor()
            result = self._parse_expr(tokens, state, default_sheet, index, cache, stack)
            # Anything trailing the parsed expression means the formula was
            # malformed -- a silent partial parse is how "=SUM(A1:A5))" reports
            # a plausible number.
            if state.pos < len(tokens):
                raise _LinterError.syntax(
                    f"unexpected {_describe_token(tokens[state.pos])} after a complete expression"
                )
            return result
        except _LinterError as error:
            if error.cause is not None:
                self._name_causes[key] = (error.cause, error.detail)
            return error.error_code
        except Exception as error:  # noqa: BLE001 - fail soft, the linter never crashes a write
            # A bug in this linter, not a fault in the formula. Still reported,
            # so it is not silently passed, but the hint says whose fault it is.
            self._name_causes[key] = ("internal", type(error).__name__)
            return ERR_NAME

    # ------------------------------------------------------------------ #
    # Tokenizer                                                           #
    # ------------------------------------------------------------------ #

    def _tokenize(self, src: str) -> list[tuple[str, str]]:
        tokens: list[tuple[str, str]] = []
        i = 0
        length = len(src)
        while i < length:
            char = src[i]
            if char in _SPACE:
                i += 1
                continue

            if char in _DIGIT or (char == "." and i + 1 < length and src[i + 1] in _DIGIT):
                start = i
                while i < length and (src[i] in _DIGIT or src[i] == "."):
                    i += 1
                tokens.append(("NUMBER", src[start:i]))
                continue

            # String literal, with Excel's "" for a literal quote: "a""b" is
            # a"b. A literal that never closes is a syntax error, not text
            # running to the end of the formula.
            if char == '"':
                i += 1
                parts: list[str] = []
                while True:
                    if i >= length:
                        raise _LinterError.syntax('a text literal opens with " and never closes')
                    if src[i] == '"':
                        if i + 1 < length and src[i + 1] == '"':
                            parts.append('"')
                            i += 2
                            continue
                        i += 1
                        break
                    parts.append(src[i])
                    i += 1
                tokens.append(("STRING", "".join(parts)))
                continue

            # Quoted sheet name: 'My Sheet', with Excel's '' for a literal quote.
            # Kept with its quotes; _clean_sheet_name() unwraps it. A quote that
            # never closes is a syntax error, not a name running to the end.
            if char == "'":
                start = i
                i += 1
                while True:
                    if i >= length:
                        raise _LinterError.syntax("a quoted sheet name opens with ' and never closes")
                    if src[i] == "'":
                        if i + 1 < length and src[i + 1] == "'":
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                tokens.append(("SHEET", src[start:i]))
                continue

            # Identifier: cell ref, function name, or sheet name. `$` is allowed
            # so `$A$1` tokenizes as one identifier.
            if char in _ALPHA or char in ("_", "$"):
                start = i
                while i < length and (src[i] in _ALNUM or src[i] in ("_", "$", ".")):
                    i += 1
                tokens.append(("IDENT", src[start:i]))
                continue

            if i + 1 < length and src[i : i + 2] in ("<=", ">=", "<>"):
                tokens.append(("OP", src[i : i + 2]))
                i += 2
                continue

            if char in "+-*/^%&=<>(),:!":
                tokens.append(("OP", char))
                i += 1
                continue

            # Unknown character: fail soft as #NAME?. Named only when it is
            # ASCII, as PHP does, so the hint is byte-identical across the three.
            raise _LinterError.syntax(
                f"unexpected character '{char}'"
                if ord(char) < 128
                else "unexpected non-ASCII character outside a text literal"
            )
        return tokens

    # ------------------------------------------------------------------ #
    # Parser (recursive descent)                                          #
    # ------------------------------------------------------------------ #

    def _parse_expr(self, tokens, state, sheet, index, cache, stack) -> Any:
        left = self._parse_concat(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] in ("=", "<", ">", "<=", ">=", "<>")
        ):
            op = tokens[state.pos][1]
            state.pos += 1
            right = self._parse_concat(tokens, state, sheet, index, cache, stack)
            left = None if self._skipping > 0 else self._compare(left, right, op)
        return left

    def _parse_concat(self, tokens, state, sheet, index, cache, stack) -> Any:
        left = self._parse_arith(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "&"
        ):
            state.pos += 1
            right = self._parse_arith(tokens, state, sheet, index, cache, stack)
            left = (
                None
                if self._skipping > 0
                else self._coerce_string(left) + self._coerce_string(right)
            )
        return left

    def _parse_arith(self, tokens, state, sheet, index, cache, stack) -> Any:
        left = self._parse_term(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] in ("+", "-")
        ):
            op = tokens[state.pos][1]
            state.pos += 1
            right = self._parse_term(tokens, state, sheet, index, cache, stack)
            if self._skipping > 0:
                left = None
                continue
            a = self._coerce_number(left)
            b = self._coerce_number(right)
            left = a + b if op == "+" else a - b
        return left

    def _parse_term(self, tokens, state, sheet, index, cache, stack) -> Any:
        left = self._parse_power(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] in ("*", "/")
        ):
            op = tokens[state.pos][1]
            state.pos += 1
            right = self._parse_power(tokens, state, sheet, index, cache, stack)
            if self._skipping > 0:
                left = None
                continue
            a = self._coerce_number(left)
            b = self._coerce_number(right)
            if op == "*":
                left = a * b
            else:
                if b == 0.0:
                    raise _LinterError(ERR_DIV0)
                left = a / b
        return left

    def _parse_power(self, tokens, state, sheet, index, cache, stack) -> Any:
        """`^` binds tighter than `*` and `/` (=2*3^2 is 18).

        As in Excel it is left-associative (=2^3^2 is 64), and negation binds
        tighter than `^`, so =-2^2 is 4: Excel's order, not mathematics'.
        """
        left = self._parse_unary(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "^"
        ):
            state.pos += 1
            right = self._parse_unary(tokens, state, sheet, index, cache, stack)
            left = (
                None
                if self._skipping > 0
                else self._coerce_number(left) ** self._coerce_number(right)
            )
        return left

    def _parse_unary(self, tokens, state, sheet, index, cache, stack) -> Any:
        if (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "-"
        ):
            state.pos += 1
            value = self._parse_unary(tokens, state, sheet, index, cache, stack)
            return None if self._skipping > 0 else -self._coerce_number(value)
        if (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "+"
        ):
            state.pos += 1
            return self._parse_unary(tokens, state, sheet, index, cache, stack)
        return self._parse_percent(tokens, state, sheet, index, cache, stack)

    def _parse_percent(self, tokens, state, sheet, index, cache, stack) -> Any:
        """Excel's `%` is a POSTFIX operator that divides by 100.

        `50%` is 0.5 and `A1%` is A1/100. It binds tighter than `^`, `*` and
        `/`. There is no binary `%`, so `10%2` leaves a stray `2` and is a
        syntax error.
        """
        value = self._parse_primary(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "%"
        ):
            state.pos += 1
            value = None if self._skipping > 0 else self._coerce_number(value) / 100.0
        return value

    def _parse_primary(self, tokens, state, sheet, index, cache, stack) -> Any:
        if state.pos >= len(tokens):
            raise _LinterError.syntax("the formula ends where a value was expected")
        kind, text = tokens[state.pos]

        if kind == "OP" and text == "(":
            state.pos += 1
            value = self._parse_expr(tokens, state, sheet, index, cache, stack)
            self._expect_op(tokens, state, ")")
            return value

        if kind == "NUMBER":
            state.pos += 1
            return float(text)
        if kind == "STRING":
            state.pos += 1
            return text

        # A quoted name is only ever a sheet qualifier. On its own it is not a
        # value, so it falls through to #NAME? below.
        if kind in ("SHEET", "IDENT"):
            # Sheet!Ref: (SHEET | IDENT) '!' IDENT
            if (
                state.pos + 2 < len(tokens)
                and tokens[state.pos + 1] == ("OP", "!")
                and tokens[state.pos + 2][0] == "IDENT"
            ):
                sheet_name = self._sheet_names.get(_clean_sheet_name(text).lower())
                if sheet_name is None and self._skipping == 0:
                    raise _LinterError(ERR_REF)
                state.pos += 2
                start_token = tokens[state.pos][1]
                state.pos += 1
                return self._resolve_ref_or_range(
                    start_token, sheet_name or text, tokens, state, index, cache, stack
                )

            if kind == "SHEET":
                raise _LinterError.syntax(
                    f"the quoted name {text} is not followed by ! and a cell reference"
                )

            # Function call
            if (
                state.pos + 1 < len(tokens)
                and tokens[state.pos + 1] == ("OP", "(")
            ):
                name = text.upper()
                # Excel resolves names before it evaluates anything, so an
                # unknown function is #NAME? whatever its arguments hold and
                # whichever IF branch it sits in.
                if name not in _FUNCTIONS:
                    raise _LinterError(ERR_NAME, "function", name)
                if name == "IF":
                    return self._parse_if(tokens, state, sheet, index, cache, stack)
                state.pos += 2
                args: list[Any] = []
                if not (
                    state.pos < len(tokens) and tokens[state.pos] == ("OP", ")")
                ):
                    while True:
                        args.append(
                            self._parse_expr(tokens, state, sheet, index, cache, stack)
                        )
                        if state.pos < len(tokens) and tokens[state.pos] == ("OP", ","):
                            state.pos += 1
                            continue
                        break
                self._expect_op(tokens, state, ")")
                return None if self._skipping > 0 else self._call_function(name, args)

            upper = text.upper()
            if upper in ("TRUE", "FALSE"):
                state.pos += 1
                return upper == "TRUE"

            state.pos += 1
            return self._resolve_ref_or_range(
                text, sheet, tokens, state, index, cache, stack
            )

        raise _LinterError.syntax(
            f"unexpected {_describe_token(tokens[state.pos])} where a value was expected"
        )

    def _parse_if(self, tokens, state, sheet, index, cache, stack) -> Any:
        """IF, evaluated lazily as Excel does.

        `=IF(A2=0,0,1/A2)` with A2 = 0 is 0, not #DIV/0!. Every argument is
        parsed, so a syntax error or an unknown function anywhere in it is
        still reported, but only the condition and the branch it selects are
        evaluated.

        The order of the verdicts is deliberate. A syntax error anywhere wins,
        because Excel refuses a formula it cannot parse and computes nothing.
        Then fewer than two arguments is #VALUE!. Then an error in the
        condition propagates, and neither branch is evaluated.
        """
        state.pos += 2  # consume IF '('
        args: list[Any] = []
        take: int | None = None  # the argument the condition selects: 1 or 2
        condition_error: _LinterError | None = None  # an error the condition evaluated to
        if not (state.pos < len(tokens) and tokens[state.pos] == ("OP", ")")):
            while True:
                i = len(args)
                skip = i > 0 and i != take
                if skip:
                    self._skipping += 1
                try:
                    args.append(self._parse_expr(tokens, state, sheet, index, cache, stack))
                finally:
                    if skip:
                        self._skipping -= 1
                if i == 0 and self._skipping == 0:
                    try:
                        take = 1 if self._to_logical(args[0]) else 2
                    except _LinterError as error:
                        condition_error = error
                if state.pos < len(tokens) and tokens[state.pos] == ("OP", ","):
                    state.pos += 1
                    continue
                break
        self._expect_op(tokens, state, ")")

        if self._skipping > 0:
            return None
        if len(args) < 2:
            return ERR_VALUE
        if condition_error is not None:
            raise condition_error
        if take == 1:
            return args[1]
        # PHP's `$args[2] ?? false`: a blank else branch reads as FALSE, as a
        # missing one does.
        return args[2] if len(args) > 2 and args[2] is not None else False

    def _resolve_ref_or_range(
        self, start_ref, default_sheet, tokens, state, index, cache, stack
    ) -> Any:
        is_range = (
            state.pos < len(tokens)
            and tokens[state.pos] == ("OP", ":")
            and state.pos + 1 < len(tokens)
            and tokens[state.pos + 1][0] == "IDENT"
        )
        if self._skipping > 0:
            # In an untaken IF branch: consume a range's `:` and end so the
            # parse carries on, but read nothing. A #REF!, or an error held by
            # the cell, belongs to a branch Excel never evaluates.
            if is_range:
                state.pos += 2
            return None
        clean_start = _clean_ref(start_ref)
        if clean_start is None:
            raise _LinterError(ERR_REF)
        if is_range:
            end_ref = tokens[state.pos + 1][1]
            state.pos += 2
            clean_end = _clean_ref(end_ref)
            if clean_end is None:
                raise _LinterError(ERR_REF)
            return self._resolve_range(
                default_sheet, clean_start, clean_end, index, cache, stack
            )
        return self._resolve_cell(default_sheet, clean_start, index, cache, stack)

    def _resolve_cell(self, sheet, a1, index, cache, stack) -> Any:
        key = f"{sheet}!{a1}"
        if key in stack:
            return ERR_CIRC
        if key in cache:
            return cache[key]
        cell = index.get(key)
        if cell is None:
            # Undefined cell -- empty in Excel. None here, so coerce_number sees
            # 0 and coerce_string sees "", consistently.
            return None
        if cell.formula is not None:
            result = self._evaluate(cell.formula, sheet, index, cache, stack + [key])
            cache[key] = result
            return result
        return cell.value

    def _resolve_range(self, sheet, start, end, index, cache, stack) -> list[Any]:
        a = CellAddress.parse(start)
        b = CellAddress.parse(end)
        if a is None or b is None:
            raise _LinterError(ERR_REF)
        col1, row1 = min(a[0], b[0]), min(a[1], b[1])
        col2, row2 = max(a[0], b[0]), max(a[1], b[1])

        values: list[Any] = []
        for r in range(row1, row2 + 1):
            for c in range(col1, col2 + 1):
                values.append(
                    self._resolve_cell(sheet, CellAddress.letter(c) + str(r), index, cache, stack)
                )
        return values

    def _expect_op(self, tokens, state, op: str) -> None:
        if state.pos >= len(tokens):
            raise _LinterError.syntax(f"expected '{op}', but the formula ends")
        if tokens[state.pos] != ("OP", op):
            raise _LinterError.syntax(
                f"expected '{op}', found {_describe_token(tokens[state.pos])}"
            )
        state.pos += 1

    # ------------------------------------------------------------------ #
    # Coercion, comparison, functions                                     #
    # ------------------------------------------------------------------ #

    def _coerce_number(self, value: Any) -> float:
        if _is_error(value):
            raise _LinterError(value)
        if isinstance(value, list):
            # Implicit intersection: first numeric wins, otherwise #VALUE!.
            for item in value:
                if isinstance(item, bool):  # before int, always
                    continue
                if isinstance(item, (int, float)):
                    return float(item)
                if isinstance(item, str) and is_numeric_string(item):
                    return float(item.strip())
            raise _LinterError(ERR_VALUE)
        if isinstance(value, bool):  # before int, always
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if value is None:
            return 0.0
        if isinstance(value, str) and is_numeric_string(value):
            return float(value.strip())
        raise _LinterError(ERR_VALUE)

    def _coerce_string(self, value: Any) -> str:
        if _is_error(value):
            raise _LinterError(value)
        if value is None:
            return ""
        if isinstance(value, bool):  # before int, always
            return "TRUE" if value else "FALSE"
        if isinstance(value, list):
            return "".join(self._coerce_string(item) for item in value)
        return php_to_string(value)

    def _compare(self, a: Any, b: Any, op: str) -> bool:
        cmp = _cmp_val(a, b)
        return {
            "=": cmp == 0,
            "<>": cmp != 0,
            "<": cmp < 0,
            ">": cmp > 0,
            "<=": cmp <= 0,
            ">=": cmp >= 0,
        }.get(op, False)

    def _to_logical(self, value: Any) -> bool:
        """Excel's reading of an IF condition.

        An error propagates, a blank is FALSE, a number is TRUE unless it is 0,
        and text is TRUE or FALSE only when it spells one of them (in any
        case). Any other text, "" included, is #VALUE!.
        """
        if _is_error(value):
            raise _LinterError(value)
        if isinstance(value, bool):  # before int, always
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            upper = value.upper()
            if upper == "TRUE":
                return True
            if upper == "FALSE":
                return False
            raise _LinterError(ERR_VALUE)
        # A range. Implicit intersection is out of scope; unchanged from before.
        return True

    def _call_function(self, name: str, args: list[Any]) -> Any:
        flat = _flatten(args)
        if name == "SUM":
            return sum(_maybe_num(v) for v in flat if _is_numeric_like(v))
        if name in ("AVERAGE", "AVG"):
            return _avg(flat)
        if name == "COUNT":
            return len([v for v in flat if _is_numeric_like(v)])
        if name == "COUNTA":
            return len([v for v in flat if v is not None and v != ""])
        if name == "MIN":
            return _min_max(flat, True)
        if name == "MAX":
            return _min_max(flat, False)
        if name == "ROUND":
            # php_round, never the builtin: Python's round() is banker's and
            # would make ROUND(2.5, 0) evaluate to 2.
            return php_round(
                self._coerce_number(args[0] if len(args) > 0 else 0),
                int(self._coerce_number(args[1] if len(args) > 1 else 0)),
            )
        if name == "ABS":
            return abs(self._coerce_number(args[0] if args else 0))
        if name == "LEN":
            return _excel_length(self._coerce_string(args[0] if args else ""))
        if name == "UPPER":
            return self._coerce_string(args[0] if args else "").upper()
        if name == "LOWER":
            return self._coerce_string(args[0] if args else "").lower()
        if name in ("CONCAT", "CONCATENATE"):
            return "".join(self._coerce_string(v) for v in flat)
        if name == "TRUE":
            return True
        if name == "FALSE":
            return False
        # Unreachable: _parse_primary checks _FUNCTIONS before parsing args,
        # and IF never arrives here, because _parse_if evaluates it lazily.
        raise _LinterError(ERR_NAME, "function", name)

    # ------------------------------------------------------------------ #
    # Hints                                                               #
    # ------------------------------------------------------------------ #

    def _hint(self, error, key, formula, sheet, index, cache) -> str:
        if error == ERR_VALUE:
            return self._hint_value(formula, sheet, index, cache)
        if error == ERR_REF:
            return self._hint_ref(formula)
        if error == ERR_NAME:
            return self._hint_name(key, formula, sheet, cache)
        if error == ERR_DIV0:
            return "Division by zero — the divisor evaluated to 0."
        if error == ERR_CIRC:
            return (
                "Circular reference — the formula directly or transitively depends "
                "on its own cell."
            )
        return "Formula evaluation failed."

    def _hint_name(self, key, formula, sheet, cache) -> str:
        """#NAME? has three causes in this formula, plus inheritance.

        Each hint says which one it is. A syntax error used to be told it
        "references an unknown function", and an agent went hunting for a
        function that was fine. The texts are byte-identical to PHP's: see PHP
        `FormulaLinter::hintName` for why a syntax error keeps the label.
        """
        cause = self._name_causes.get(key)
        if cause is not None and cause[0] == "syntax":
            return (
                f"Syntax error: {cause[1]}. Excel cannot parse this formula. Check for an "
                "unbalanced parenthesis or quote, a missing operator or argument, or a character "
                "a formula cannot contain. Text goes in double quotes, and a quote inside text "
                'is written twice: "a""b".'
            )
        if cause is not None and cause[0] == "function":
            return (
                f"Unknown function {cause[1]}. Holy Sheet supports: SUM, AVERAGE, COUNT, "
                "COUNTA, MIN, MAX, IF, ROUND, ABS, LEN, UPPER, LOWER, CONCAT."
            )
        if cause is not None and cause[0] == "internal":
            return (
                f"The linter itself failed on this formula ({cause[1]}). That is a holy-sheet "
                "defect, not evidence that the formula is wrong."
            )
        return self._hint_inherited_name(formula, sheet, cache)

    def _hint_inherited_name(self, formula, sheet, cache) -> str:
        sources: list[str] = []
        for match in _HINT_REFS.finditer(formula):
            if match.group(1):
                written = _clean_sheet_name(match.group(1))
                sheet_name = self._sheet_names.get(written.lower(), written)
            else:
                sheet_name = sheet
            a1 = match.group(2).upper() + match.group(3)
            if cache.get(f"{sheet_name}!{a1}") == ERR_NAME:
                source = f"{match.group(1)}!{a1}" if match.group(1) else a1
                if source not in sources:
                    sources.append(source)
        if not sources:
            return "This formula depends on a cell whose value is #NAME?. Fix that cell first."
        return (
            f"This formula depends on {', '.join(sources)}, whose value is #NAME?. "
            "Fix that cell first."
        )

    def _hint_ref(self, formula: str) -> str:
        """Name the sheet when that is what is missing.

        "A cell doesn't exist" sends an agent to check column letters in a sheet
        it never created.
        """
        for match in _SHEET_REFS.finditer(formula):
            name = _clean_sheet_name(match.group(1))
            if name.lower() not in self._sheet_names:
                return (
                    f"The formula refers to a sheet named '{name}', and this workbook has no such "
                    f"sheet. Its sheets are: {', '.join(self._sheet_names.values())}. Quote a name "
                    "that contains spaces or punctuation: 'My Sheet'!A1."
                )
        return (
            "A cell reference points to a cell that doesn't exist in the workbook. "
            "Check column letters and row numbers."
        )

    def _hint_value(self, formula, sheet, index, cache) -> str:
        """Name the offending cell, and offer the row below when it is numeric.

        A string in arithmetic is almost always the header-row off-by-one, so
        the hint says which cell holds the text AND what the obvious fix is.
        That is the difference between an error an agent can act on and one it
        will just retry verbatim.
        """
        offenders: list[str] = []
        for match in _HINT_REFS.finditer(formula):
            if match.group(1):
                written = _clean_sheet_name(match.group(1))
                sheet_name = self._sheet_names.get(written.lower(), written)
            else:
                sheet_name = sheet
            a1 = match.group(2).upper() + match.group(3)
            key = f"{sheet_name}!{a1}"
            cell = index.get(key)
            if cell is None:
                continue
            value = cache.get(key, None if cell.formula is not None else cell.value)
            if isinstance(value, str) and not is_numeric_string(value) and value != "":
                row = int(match.group(3))
                col = match.group(2).upper()
                next_cell = index.get(f"{sheet_name}!{col}{row + 1}")
                suggestion = ""
                if next_cell is not None and is_numeric_string(next_cell.value):
                    suggestion = (
                        f" Did you mean {col}{row + 1}? "
                        f"(it holds {php_to_string(next_cell.value)})"
                    )
                offenders.append(f'{a1} = "{value}" (string){suggestion}')
        if offenders:
            return "Arithmetic on a non-numeric cell: " + "; ".join(offenders)
        return (
            "A non-numeric value was used in arithmetic. Check that all referenced "
            "cells contain numbers."
        )


class _Cursor:
    """A mutable parse position -- PHP threads `int &$pos` through the parser."""

    __slots__ = ("pos",)

    def __init__(self) -> None:
        self.pos = 0


def _is_error(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("#")


def _describe_token(token: tuple[str, str]) -> str:
    kind, text = token
    if kind == "STRING":
        return f'text "{text}"'
    if kind == "NUMBER":
        return f"number {text}"
    return f"'{text}'"


def _clean_ref(ref: str) -> str | None:
    """Strip `$` absolute markers; uppercase A1, or None when malformed."""
    cleaned = ref.replace("$", "").upper()
    return cleaned if _CLEAN_REF.match(cleaned) else None


def _clean_sheet_name(name: str) -> str:
    """Unwrap the quotes Excel puts around a sheet name that needs them.

    A doubled quote inside becomes one: 'Q3 ''Final''' is Q3 'Final'.
    """
    if len(name) >= 2 and name[0] == "'" and name[-1] == "'":
        return name[1:-1].replace("''", "'")
    return name


def _excel_length(text: str) -> int:
    """Excel's LEN counts UTF-16 code units, as PHP's and JS's do.

    `len()` counts code points, so an emoji outside the Basic Multilingual
    Plane was 1 here and 2 in Excel. `surrogatepass` keeps a lone surrogate
    (which JSON can carry) at 1 unit instead of raising.
    """
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _blank_like(other: Any) -> Any:
    """What a blank cell reads as when compared with `other`."""
    if isinstance(other, bool):  # before int, always
        return False
    if isinstance(other, str):
        return ""
    return 0.0


def _rank(value: Any) -> int:
    if isinstance(value, bool):  # before int, always
        return 2
    return 1 if isinstance(value, str) else 0


def _cmp_val(a: Any, b: Any) -> int:
    """Excel's comparison.

    An error on either side propagates. Otherwise the types rank number < text
    < logical, so ="5">9 is TRUE: numeric TEXT is text, never a number. Text
    compares without regard to case, by code point (PHP's strcmp on UTF-8). A
    blank takes the other side's type (0, "" or FALSE); two blanks are equal.
    """
    if _is_error(a):
        raise _LinterError(a)
    if _is_error(b):
        raise _LinterError(b)
    if isinstance(a, list) or isinstance(b, list):
        # A range. Implicit intersection is out of scope; unchanged from before.
        left, right = php_to_string(a), php_to_string(b)
    else:
        if a is None:
            a = _blank_like(b)
        if b is None:
            b = _blank_like(a)
        if _rank(a) != _rank(b):
            return -1 if _rank(a) < _rank(b) else 1
        if isinstance(a, str):
            left, right = a.lower(), b.lower()
        else:
            left, right = a, b
    if left < right:
        return -1
    return 1 if left > right else 0


def _flatten(args: list[Any]) -> list[Any]:
    out: list[Any] = []
    for arg in args:
        if isinstance(arg, list):
            out.extend(arg)
        else:
            out.append(arg)
    return out


def _is_numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return True  # Excel counts booleans in SUM/COUNT
    return isinstance(value, (int, float)) or (
        isinstance(value, str) and is_numeric_string(value)
    )


def _maybe_num(value: Any) -> float:
    if isinstance(value, bool):  # before int, always
        return 1.0 if value else 0.0
    return float(value)


def _avg(flat: list[Any]) -> float | str:
    numbers = [v for v in flat if _is_numeric_like(v)]
    if not numbers:
        return ERR_DIV0
    return sum(_maybe_num(v) for v in numbers) / len(numbers)


def _min_max(flat: list[Any], want_min: bool) -> float:
    numbers = [_maybe_num(v) for v in flat if _is_numeric_like(v)]
    if not numbers:
        return 0.0
    return min(numbers) if want_min else max(numbers)
