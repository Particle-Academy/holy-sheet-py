"""Evaluate every formula in a schema and report the ones that break.

The linter exists to catch the four bugs an LLM actually introduces with
formulas: referencing the header row instead of the first data row, passing a
string to a numeric operator, citing a cell that does not exist, and building a
circular dependency. Catching those BEFORE the xlsx is written lets an agent
self-correct on the next loop iteration instead of shipping a file whose numbers
are `#VALUE!`.

Supported vocabulary -- the subset agents actually emit:

* operators ``+ - * / ^ % & = < > <= >= <>``
* cell refs ``A1``, ``$A$1``, ``Sheet2!B5``; ranges ``A1:A10``
* numeric / string literals, parenthesised expressions
* functions SUM, AVERAGE/AVG, COUNT, COUNTA, MIN, MAX, IF, ROUND, ABS, LEN,
  UPPER, LOWER, CONCAT/CONCATENATE

Out of scope: array formulas, dynamic arrays, structured table refs, named
ranges, and the rest of Excel's 400+ functions. Those return ``#NAME?`` so the
agent learns to avoid them rather than shipping something that silently differs.
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

_CLEAN_REF = re.compile(r"^[A-Z]+\d+$")
_HINT_REFS = re.compile(r"(?:([A-Za-z][A-Za-z0-9_]*)!)?\$?([A-Z]+)\$?(\d+)", re.IGNORECASE)


class _LinterError(Exception):
    """Short-circuit for an Excel error raised deep inside the parser."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class FormulaLinter:
    def lint(self, schema: Any) -> list[dict[str, str]]:
        workbook = Normalizer().normalize(schema)
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
                            "hint": self._hint(result, cell.formula, sheet.name, index, cache),
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
        try:
            tokens = self._tokenize(formula)
            state = _Cursor()
            result = self._parse_expr(tokens, state, default_sheet, index, cache, stack)
            # Anything trailing the parsed expression means the formula was
            # malformed -- a silent partial parse is how "=SUM(A1:A5))" reports
            # a plausible number.
            if state.pos < len(tokens):
                return ERR_NAME
            return result
        except _LinterError as error:
            return error.error_code
        except Exception:  # noqa: BLE001 - fail soft, the linter never crashes a write
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
            if char.isspace():
                i += 1
                continue

            if char.isdigit() or (char == "." and i + 1 < length and src[i + 1].isdigit()):
                start = i
                while i < length and (src[i].isdigit() or src[i] == "."):
                    i += 1
                tokens.append(("NUMBER", src[start:i]))
                continue

            if char == '"':
                i += 1
                start = i
                while i < length and src[i] != '"':
                    i += 1
                tokens.append(("STRING", src[start:i]))
                i += 1  # consume the closing quote
                continue

            # Identifier: cell ref, function name, or sheet name. `$` is allowed
            # so `$A$1` tokenizes as one identifier.
            if char.isalpha() or char in ("_", "$"):
                start = i
                while i < length and (src[i].isalnum() or src[i] in ("_", "$", ".")):
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

            raise _LinterError(ERR_NAME)
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
            left = self._compare(left, right, op)
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
            left = self._coerce_string(left) + self._coerce_string(right)
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
            a = self._coerce_number(left)
            b = self._coerce_number(right)
            left = a + b if op == "+" else a - b
        return left

    def _parse_term(self, tokens, state, sheet, index, cache, stack) -> Any:
        left = self._parse_unary(tokens, state, sheet, index, cache, stack)
        while (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] in ("*", "/", "%", "^")
        ):
            op = tokens[state.pos][1]
            state.pos += 1
            right = self._parse_unary(tokens, state, sheet, index, cache, stack)
            a = self._coerce_number(left)
            b = self._coerce_number(right)
            if op == "*":
                left = a * b
            elif op == "/":
                if b == 0.0:
                    raise _LinterError(ERR_DIV0)
                left = a / b
            elif op == "%":
                # Excel's `%` is a postfix /100; treated as a binary multiply
                # here, which is the reference engine's simplification.
                left = a / 100.0 * b
            else:
                left = a**b
        return left

    def _parse_unary(self, tokens, state, sheet, index, cache, stack) -> Any:
        if (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "-"
        ):
            state.pos += 1
            value = self._parse_unary(tokens, state, sheet, index, cache, stack)
            return -self._coerce_number(value)
        if (
            state.pos < len(tokens)
            and tokens[state.pos][0] == "OP"
            and tokens[state.pos][1] == "+"
        ):
            state.pos += 1
            return self._parse_unary(tokens, state, sheet, index, cache, stack)
        return self._parse_primary(tokens, state, sheet, index, cache, stack)

    def _parse_primary(self, tokens, state, sheet, index, cache, stack) -> Any:
        if state.pos >= len(tokens):
            raise _LinterError(ERR_NAME)
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

        if kind == "IDENT":
            # Sheet!Ref
            if (
                state.pos + 2 < len(tokens)
                and tokens[state.pos + 1] == ("OP", "!")
                and tokens[state.pos + 2][0] == "IDENT"
            ):
                sheet_name = _clean_sheet_name(text)
                state.pos += 2
                start_token = tokens[state.pos][1]
                state.pos += 1
                return self._resolve_ref_or_range(
                    start_token, sheet_name, tokens, state, index, cache, stack
                )

            # Function call
            if (
                state.pos + 1 < len(tokens)
                and tokens[state.pos + 1] == ("OP", "(")
            ):
                name = text.upper()
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
                return self._call_function(name, args)

            upper = text.upper()
            if upper in ("TRUE", "FALSE"):
                state.pos += 1
                return upper == "TRUE"

            state.pos += 1
            return self._resolve_ref_or_range(
                text, sheet, tokens, state, index, cache, stack
            )

        raise _LinterError(ERR_NAME)

    def _resolve_ref_or_range(
        self, start_ref, default_sheet, tokens, state, index, cache, stack
    ) -> Any:
        clean_start = _clean_ref(start_ref)
        if clean_start is None:
            raise _LinterError(ERR_REF)
        if (
            state.pos < len(tokens)
            and tokens[state.pos] == ("OP", ":")
            and state.pos + 1 < len(tokens)
            and tokens[state.pos + 1][0] == "IDENT"
        ):
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
        if state.pos >= len(tokens) or tokens[state.pos] != ("OP", op):
            raise _LinterError(ERR_NAME)
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
        if name == "IF":
            return _if_fn(args)
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
            return len(self._coerce_string(args[0] if args else ""))
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
        return ERR_NAME

    # ------------------------------------------------------------------ #
    # Hints                                                               #
    # ------------------------------------------------------------------ #

    def _hint(self, error, formula, sheet, index, cache) -> str:
        if error == ERR_VALUE:
            return self._hint_value(formula, sheet, index, cache)
        if error == ERR_REF:
            return (
                "A cell reference points to a cell that doesn't exist in the workbook. "
                "Check column letters and row numbers."
            )
        if error == ERR_NAME:
            return (
                "The formula references an unknown function or has a syntax error. "
                "Holy Sheet supports: SUM, AVERAGE, COUNT, COUNTA, MIN, MAX, IF, ROUND, "
                "ABS, LEN, UPPER, LOWER, CONCAT."
            )
        if error == ERR_DIV0:
            return "Division by zero — the divisor evaluated to 0."
        if error == ERR_CIRC:
            return (
                "Circular reference — the formula directly or transitively depends "
                "on its own cell."
            )
        return "Formula evaluation failed."

    def _hint_value(self, formula, sheet, index, cache) -> str:
        """Name the offending cell, and offer the row below when it is numeric.

        A string in arithmetic is almost always the header-row off-by-one, so
        the hint says which cell holds the text AND what the obvious fix is.
        That is the difference between an error an agent can act on and one it
        will just retry verbatim.
        """
        offenders: list[str] = []
        for match in _HINT_REFS.finditer(formula):
            sheet_name = match.group(1) or sheet
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


def _clean_ref(ref: str) -> str | None:
    """Strip `$` absolute markers; uppercase A1, or None when malformed."""
    cleaned = ref.replace("$", "").upper()
    return cleaned if _CLEAN_REF.match(cleaned) else None


def _clean_sheet_name(name: str) -> str:
    """Strip the single quotes Excel wraps around sheet names with spaces."""
    if len(name) >= 2 and name[0] == "'" and name[-1] == "'":
        return name[1:-1]
    return name


def _cmp_val(a: Any, b: Any) -> int:
    a_numeric = (not isinstance(a, bool)) and (
        isinstance(a, (int, float)) or (isinstance(a, str) and is_numeric_string(a))
    )
    b_numeric = (not isinstance(b, bool)) and (
        isinstance(b, (int, float)) or (isinstance(b, str) and is_numeric_string(b))
    )
    if a_numeric and b_numeric:
        left, right = float(a), float(b)
    else:
        left, right = php_to_string(a), php_to_string(b)
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


def _if_fn(args: list[Any]) -> Any:
    if len(args) < 2:
        return ERR_VALUE
    cond = args[0]
    if isinstance(cond, bool):
        truthy = cond
    else:
        truthy = cond is not None and cond != 0 and cond != "" and cond != "FALSE"
    if truthy:
        return args[1]
    return args[2] if len(args) > 2 else False
