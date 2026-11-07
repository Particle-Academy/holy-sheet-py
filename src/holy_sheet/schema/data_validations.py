"""Data validation: the schema shape, its problems, its normal form and its XML.

A line-by-line port of PHP ``Schema\\DataValidations``. The error paths, wording
and check order are PHP's, and so are the bytes: ``test_parity_php.py`` diffs
the worksheet part against PHP's writer on the ``dataValidations`` fixture.

Python traps handled here, each the one the AGENTS.md "Numbers" section warns
about: ``bool`` is checked before ``int`` everywhere a type is branched on; a
key that is present with ``None`` counts as present (PHP's ``array_key_exists``,
not ``isset``); and a decimal operand goes through ``format_float``, never
``str()``. Lengths are ``len()`` of a ``str``, which counts code points, as
PHP's ``mb_strlen`` does.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from ..helpers.php import PHP_INT_MAX, PHP_INT_MIN, format_float, type_of
from ..workbook.cell_address import CellAddress
from ..workbook.chart_range import ChartRange
from ..writer.format.date_converter import DateConverter
from . import column_widths

TYPES = ["list", "whole", "decimal", "date", "textLength"]
OPERATORS = ["between", "notBetween", "equal", "notEqual", "greaterThan", "lessThan", "greaterThanOrEqual", "lessThanOrEqual"]
STYLES = ["stop", "warning", "information"]

MAX_LIST = 255
_MAX_WHOLE = 9007199254740991
_MAX_ROW = 1048576
_ISO_DATE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})$")

Problem = tuple[str, str, str, Any, str]


def _is_obj(value: Any) -> bool:
    """PHP's array as an object: a dict, or an EMPTY list ([] and {} are one PHP value)."""
    return isinstance(value, dict) or (isinstance(value, (list, tuple)) and len(value) == 0)


def _is_list(value: Any) -> bool:
    """PHP's array as a list: a list, or an EMPTY dict."""
    return isinstance(value, (list, tuple)) or (isinstance(value, dict) and len(value) == 0)


def problems(dv: Any, own_sheet: str, sheet_names: list[str] | None, column: bool, data_rows: int) -> list[Problem]:
    """The problems with one rule, as (subpath, expected, got, value, hint)."""
    if not _is_obj(dv):
        return [("", "object", type_of(dv), dv,
                 'A column validation is an object: {"type": "list", "values": ["Open", "Closed"]}.' if column
                 else 'A validation is an object: {"range": "B2:B100", "type": "list", "values": ["Open", "Closed"]}.')]
    d: dict[str, Any] = dv if isinstance(dv, dict) else {}
    out: list[Problem] = []
    kind = d.get("type")
    known = isinstance(kind, str) and kind in TYPES
    if not known:
        out.append((".type", "one of: " + ", ".join(TYPES), type_of(kind), kind,
                    "list is a dropdown; whole, decimal and date bound a number or a date; textLength bounds how long the text is."))

    if column:
        if "range" in d:
            out.append((".range", "absent", type_of(d["range"]), d["range"],
                        "A column validation covers that column's data rows. To choose the cells yourself, put it in the sheet's dataValidations with a range."))
        elif data_rows == 0:
            out.append(("", "a sheet with data rows", "no rows", None,
                        "A column validation covers the column's data rows, and this sheet has none. For a template, put it in the sheet's dataValidations with a range, like \"B2:B500\"."))
    elif sqref(d.get("range")) is None:
        out.append((".range", 'a cell or range on this sheet, like "B2:B100"', type_of(d.get("range")), d.get("range"),
                    'The cells the rule applies to, on this sheet. A dropdown for a template column usually runs well past the sample rows: "B2:B500".'))

    if kind == "list":
        out.extend(_list_problems(d, own_sheet, sheet_names))
    elif known:
        out.extend(_bound_problems(d, kind))

    if "allowBlank" in d and not isinstance(d["allowBlank"], bool):
        out.append((".allowBlank", "boolean", type_of(d["allowBlank"]), d["allowBlank"],
                    "true (the default) lets a cell be left empty; false makes an empty cell break the rule."))

    if "prompt" in d:
        p = d["prompt"]
        if not _is_obj(p):
            out.append((".prompt", "object {title?, text?}", type_of(p), p,
                        'Shown when the cell is selected: {"title": "Status", "text": "Pick one."}.'))
        else:
            out.extend(_text_problems(p if isinstance(p, dict) else {}, ".prompt", 255, "Excel limits an input prompt to 255 characters."))

    if "error" in d:
        e = d["error"]
        if not _is_obj(e):
            out.append((".error", "object {style?, title?, text?}", type_of(e), e,
                        'Shown when an entry breaks the rule: {"style": "stop", "title": "Invalid status", "text": "Pick a status from the list."}.'))
        else:
            e = e if isinstance(e, dict) else {}
            if "style" in e and not (isinstance(e["style"], str) and e["style"] in STYLES):
                out.append((".error.style", "one of: " + ", ".join(STYLES), type_of(e["style"]), e["style"],
                            "stop refuses the entry (the default); warning lets the person keep it after a warning; information only tells them."))
            out.extend(_text_problems(e, ".error", 225, "Excel limits an error message to 225 characters."))

    return out


def _list_problems(d: dict[str, Any], own_sheet: str, sheet_names: list[str] | None) -> list[Problem]:
    out: list[Problem] = []
    has_values = "values" in d
    has_source = "source" in d
    if has_values == has_source:
        out.append(("", "exactly one of values or source", "both" if has_values else "neither", None,
                    "A dropdown lists its choices inline, {\"values\": [\"Open\", \"Closed\"]}, or reads them from cells, {\"source\": \"'Lists'!A1:A10\"}."))
    elif has_values:
        values = d["values"]
        if not isinstance(values, (list, tuple)) or len(values) == 0:
            out.append((".values", "a non-empty list of choices", type_of(values), values, 'The choices, in order: ["Open", "Closed"].'))
        else:
            bad = False
            for k, v in enumerate(values):
                if _choice(v) is None:
                    bad = True
                    out.append((f".values[{k}]", "a non-empty string with no comma, or a whole number", type_of(v), v,
                                'Each choice is text, like "Open", or a whole number. Excel separates inline choices with commas and cannot escape one, so a choice containing a comma must come from cells: use source.'))
            if not bad:
                length = len(",".join(_choice(v) for v in values))  # type: ignore[misc]
                if length > MAX_LIST:
                    out.append((".values", "at most 255 characters, commas included", f"{length} characters", values,
                                "Excel cannot store an inline list longer than 255 characters. Put the choices in cells and point source at them, like \"'Lists'!A1:A40\"."))
    else:
        rng = _grid_range(d["source"], own_sheet)
        if rng is None:
            out.append((".source", "a cell range like \"'Lists'!A1:A10\"", type_of(d["source"]), d["source"],
                        "The cells holding the choices: \"A2:A20\" on this sheet, or \"'Lists'!A1:A10\" on another."))
        elif sheet_names is not None and rng.sheet not in sheet_names:
            out.append((".source", "a sheet in this workbook", "unknown sheet", d["source"],
                        f'There is no sheet named "{rng.sheet}". Name the sheet exactly as sheets[].name has it.'))

    if "showDropdown" in d and not isinstance(d["showDropdown"], bool):
        out.append((".showDropdown", "boolean", type_of(d["showDropdown"]), d["showDropdown"],
                    "true (the default) shows the in-cell dropdown arrow; false hides the arrow and still enforces the list."))

    for key in ("operator", "min", "max", "value"):
        if key in d:
            out.append((f".{key}", "absent", type_of(d[key]), d[key], f"{key} does not apply to a list: its choices are the rule."))
    return out


def _bound_problems(d: dict[str, Any], kind: str) -> list[Problem]:
    out: list[Problem] = []
    operator = d.get("operator")
    if operator is None:
        operator = "between"
    known = isinstance(operator, str) and operator in OPERATORS
    if not known:
        out.append((".operator", "one of: " + ", ".join(OPERATORS), type_of(operator), operator,
                    "Omit for between. between and notBetween take min and max; the others take value."))

    expected, hint = {
        "whole": ("a whole number", "A whole number, like 10."),
        "decimal": ("a number", "A number, like 0.5."),
        "date": ("a date as YYYY-MM-DD", 'A calendar date, like "2026-12-31".'),
    }.get(kind, ("a whole number of characters, 0 or more", "A length in characters, like 50."))

    if known:
        two = operator in ("between", "notBetween")
        for key in (("min", "max") if two else ("value",)):
            v = d.get(key)
            if _operand(v, kind) is None:
                out.append((f".{key}", expected, type_of(v), v, hint))
        if two and _operand(d.get("min"), kind) is not None and _operand(d.get("max"), kind) is not None \
                and _compare(d["min"], d["max"], kind) > 0:
            out.append((".max", "a value no less than min", type_of(d["max"]), d["max"],
                        "min is the lower bound and max the upper; as written, nothing could ever pass this rule."))
        for key in (("value",) if two else ("min", "max")):
            if key in d:
                out.append((f".{key}", "absent", type_of(d[key]), d[key],
                            f"{operator} takes min and max, not value." if two
                            else f"{operator} compares against one value: use value, not min and max."))

    for key in ("values", "source", "showDropdown"):
        if key in d:
            out.append((f".{key}", "absent", type_of(d[key]), d[key], f"{key} applies only to a list."))
    return out


def _text_problems(box: dict[str, Any], at: str, maximum: int, hint: str) -> list[Problem]:
    out: list[Problem] = []
    if "title" in box and (not isinstance(box["title"], str) or len(box["title"]) > 32):
        out.append((f"{at}.title", "a string of at most 32 characters", type_of(box["title"]), box["title"],
                    "Excel limits a prompt or error title to 32 characters."))
    if "text" in box and (not isinstance(box["text"], str) or len(box["text"]) > maximum):
        out.append((f"{at}.text", f"a string of at most {maximum} characters", type_of(box["text"]), box["text"], hint))
    return out


def normalize(dv: Any, ref: str, own_sheet: str) -> dict[str, Any] | None:
    """A rule in normal form, or None when it has a problem (the validator names it)."""
    # The range was checked by the caller and is carried as `ref`; the rest is
    # checked as a column rule with rows, which is every other check.
    if isinstance(dv, dict):
        dv = {k: v for k, v in dv.items() if k != "range"}
    if problems(dv, own_sheet, None, True, 1):
        return None
    kind = dv["type"]
    operator: str | None = None
    formula2: str | None = None
    if kind == "list":
        if "values" in dv:
            formula1 = '"' + ",".join(_choice(v) for v in dv["values"]).replace('"', '""') + '"'  # type: ignore[misc]
        else:
            formula1 = _source_formula(_grid_range(dv["source"], own_sheet), own_sheet)  # type: ignore[arg-type]
    else:
        op = dv.get("operator")
        if op is None:
            op = "between"
        operator = None if op == "between" else op
        if op in ("between", "notBetween"):
            formula1 = _operand(dv["min"], kind)  # type: ignore[assignment]
            formula2 = _operand(dv["max"], kind)
        else:
            formula1 = _operand(dv["value"], kind)  # type: ignore[assignment]

    prompt = None
    if isinstance(dv.get("prompt"), dict):
        prompt = {"title": _text(dv["prompt"].get("title")), "text": _text(dv["prompt"].get("text"))}
    error = dv.get("error") if isinstance(dv.get("error"), dict) else {}
    allow_blank = dv.get("allowBlank")
    show_dropdown = dv.get("showDropdown")

    return {
        "sqref": ref,
        "type": kind,
        "operator": operator,
        "formula1": formula1,
        "formula2": formula2,
        "allowBlank": (True if allow_blank is None else allow_blank) is True,
        "hideDropdown": kind == "list" and show_dropdown is False,
        "prompt": prompt,
        "error": {
            "style": "stop" if error.get("style") is None else str(error["style"]),
            "title": _text(error.get("title")),
            "text": _text(error.get("text")),
        },
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def for_sheet(sheet: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The rules of one sheet in normal form: column rules first, in column order, then the sheet's list."""
    out: list[dict[str, Any]] = []
    columns = sheet.get("columns") if isinstance(sheet.get("columns"), list) else []
    rows = len(sheet["rows"]) if isinstance(sheet.get("rows"), (list, dict)) else 0
    if rows > 0:
        for i, col in enumerate(columns):
            if not isinstance(col, dict) or "validation" not in col:
                continue
            letter = CellAddress.letter(i)
            ref = f"{letter}2" + (f":{letter}{rows + 1}" if rows > 1 else "")
            rule = col["validation"]
            if _is_obj(rule) and not (isinstance(rule, dict) and "range" in rule):
                n = normalize(rule, ref, name)
                if n is not None:
                    out.append(n)
    rules = sheet.get("dataValidations")
    if _is_list(rules):
        for dv in (rules if isinstance(rules, (list, tuple)) else []):
            ref = sqref(dv.get("range")) if isinstance(dv, dict) else None
            if ref is None:
                continue
            n = normalize(dv, ref, name)
            if n is not None:
                out.append(n)
    return out


def xml(rules: list[dict[str, Any]], escape: Callable[[str], str]) -> str:
    """``<dataValidations>``, or "" for none. Attribute order is PHP's (CT_DataValidation's)."""
    if not rules:
        return ""
    parts = [f'<dataValidations count="{len(rules)}">']
    for r in rules:
        prompt = r["prompt"]
        error = r["error"]
        parts.append(
            f'<dataValidation type="{r["type"]}"'
            + (f' errorStyle="{error["style"]}"' if error["style"] != "stop" else "")
            + (f' operator="{r["operator"]}"' if r["operator"] is not None else "")
            + (' allowBlank="1"' if r["allowBlank"] else "")
            # OOXML's showDropDown="1" HIDES the arrow; the schema's showDropdown says what a person sees.
            + (' showDropDown="1"' if r["hideDropdown"] else "")
            + (' showInputMessage="1"' if prompt is not None else "")
            + ' showErrorMessage="1"'
            + (f' errorTitle="{escape(error["title"])}"' if error["title"] != "" else "")
            + (f' error="{escape(error["text"])}"' if error["text"] != "" else "")
            + (f' promptTitle="{escape(prompt["title"])}"' if prompt is not None and prompt["title"] != "" else "")
            + (f' prompt="{escape(prompt["text"])}"' if prompt is not None and prompt["text"] != "" else "")
            + f' sqref="{r["sqref"]}">'
            + f"<formula1>{escape(r['formula1'])}</formula1>"
            + (f"<formula2>{escape(r['formula2'])}</formula2>" if r["formula2"] is not None else "")
            + "</dataValidation>"
        )
    parts.append("</dataValidations>")
    return "".join(parts)


def sqref(rng: Any) -> str | None:
    """A sheet-level range as the canonical sqref, or None when it is not one on this sheet inside the grid."""
    if not isinstance(rng, str) or "!" in rng:
        return None
    r = _grid_range(rng, "")
    return None if r is None else r.to_schema("")


def _grid_range(ref: Any, own_sheet: str) -> ChartRange | None:
    r = ChartRange.parse(ref, own_sheet)
    if r is None or r.c2 > column_widths.MAX_INDEX or r.r2 > _MAX_ROW:
        return None
    return r


def _source_formula(r: ChartRange, own_sheet: str) -> str:
    """$A$1:$A$9 on the rule's own sheet, 'Lists'!$A$1:$A$9 on another."""
    start = f"${CellAddress.letter(r.c1)}${r.r1}"
    end = f"${CellAddress.letter(r.c2)}${r.r2}"
    ref = start if start == end else f"{start}:{end}"
    return ref if r.sheet == own_sheet else "'" + r.sheet.replace("'", "''") + "'!" + ref


def _choice(v: Any) -> str | None:
    """An inline choice as text, or None when it cannot be one."""
    if isinstance(v, str):
        return v if v != "" and "," not in v else None
    if isinstance(v, bool):  # before int, always
        return None
    if isinstance(v, int) and abs(v) <= _MAX_WHOLE:
        return str(v)
    return None


def _whole(v: Any) -> int | None:
    """A number with no fraction inside the exact-integer range."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if abs(v) <= _MAX_WHOLE else None
    if isinstance(v, float) and v == v and v not in (float("inf"), float("-inf")) and v.is_integer() and abs(v) <= _MAX_WHOLE:
        return int(v)
    return None


def _operand(v: Any, kind: str) -> str | None:
    """An operand as the text of its formula, or None when it is not one for this type."""
    if kind in ("whole", "textLength"):
        w = _whole(v)
        if w is None or (kind == "textLength" and w < 0):
            return None
        return str(w)
    if kind == "decimal":
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            # An int outside zend_long is what PHP's json_decode makes a float.
            return str(v) if PHP_INT_MIN <= v <= PHP_INT_MAX else format_float(float(v))
        if not isinstance(v, float) or v != v or v in (float("inf"), float("-inf")):
            return None
        return format_float(v)
    if kind == "date":
        if not isinstance(v, str):
            return None
        m = _ISO_DATE.match(v)
        if m is None:
            return None
        try:
            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return str(int(DateConverter.to_serial(v)))
    return None


def _compare(a: Any, b: Any, kind: str) -> int:
    if kind == "date":
        return (a > b) - (a < b)
    return (float(a) > float(b)) - (float(a) < float(b))
