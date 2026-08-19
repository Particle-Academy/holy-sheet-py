"""Editor-facing shapes for the input schema.

**These are `TypedDict`s, and that is load-bearing rather than laziness.** The
input to every entry point is a plain `dict` -- the same loose JSON an agent
emits in one shot -- and the *Validator* is the gate, not the type system. A
dataclass would move the gate into a constructor and reject exactly the
malformed input `validate_and_repair` exists to fix: a singular `sheet` key,
`row` for `rows`, an integer-keyed rows object, `"1200"` where a number belongs.

Every field is optional (`total=False`) for the same reason. Use these for
autocomplete and for `mypy`; never to construct at runtime.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ThemeKey = Literal["default", "minimal", "plain", "business"]

ColumnType = Literal[
    "auto",
    "string",
    "number",
    "integer",
    "boolean",
    "date",
    "datetime",
    "currency",
    "percent",
    "formula",
]

DisplayFormat = Literal[
    "auto", "text", "number", "date", "datetime", "percentage", "currency"
]

AggOp = Literal["sum", "avg", "count", "min", "max"]

CellPrimitive = str | int | float | bool | None


class ColumnSchema(TypedDict, total=False):
    header: str
    type: ColumnType
    currency: str
    decimals: int
    format: str
    width: float


class CellFormatInput(TypedDict, total=False):
    bold: bool
    italic: bool
    textAlign: Literal["left", "center", "right"]
    displayFormat: DisplayFormat
    decimals: int
    color: str
    backgroundColor: str
    fontSize: int
    borderTop: str | None
    borderRight: str | None
    borderBottom: str | None
    borderLeft: str | None
    currency: str


class CommentInput(TypedDict, total=False):
    text: str
    author: str
    color: str


class CellData(TypedDict, total=False):
    value: CellPrimitive
    formula: str
    computedValue: CellPrimitive
    format: CellFormatInput
    comment: CommentInput


class MergedRegionInput(TypedDict, total=False):
    start: str
    end: str


class SheetSchema(TypedDict, total=False):
    name: str
    # Row-oriented mode.
    columns: list[ColumnSchema]
    rows: list[list[Any]]
    theme: ThemeKey
    totals: dict[str, AggOp | str]
    # Sparse mode. A bare scalar is allowed as well as a CellData object, and a
    # bare string beginning with "=" is promoted to a formula.
    cells: dict[str, CellData | CellPrimitive]
    # Shared.
    mergedRegions: list[MergedRegionInput]
    columnWidths: dict[int | str, float]
    frozenRows: int
    frozenCols: int


class WorkbookMeta(TypedDict, total=False):
    creator: str
    created: str


class HolySheetSchema(TypedDict, total=False):
    sheets: list[SheetSchema]
    meta: WorkbookMeta


class ValidationError(TypedDict):
    path: str
    expected: str
    got: str
    value: Any
    hint: str


class FormulaProblem(TypedDict):
    sheet: str
    address: str
    formula: str
    error: str
    hint: str


class WriteResult(TypedDict):
    path: str
    bytes: int
    sheets: int


class RepairResult(TypedDict):
    schema: dict[str, Any]
    errors: list[ValidationError]
    repairs: list[str]


class BuilderOptions(TypedDict, total=False):
    """Passthrough options for `from_array` / `from_csv`."""

    theme: ThemeKey
    currency: str
    totals: dict[str, AggOp | str]
    frozenRows: int
    frozenCols: int
    sheetName: str
    # CSV-specific.
    delimiter: str
    enclosure: str


__all__ = [
    "AggOp",
    "BuilderOptions",
    "CellData",
    "CellFormatInput",
    "CellPrimitive",
    "ColumnSchema",
    "ColumnType",
    "CommentInput",
    "DisplayFormat",
    "FormulaProblem",
    "HolySheetSchema",
    "MergedRegionInput",
    "RepairResult",
    "SheetSchema",
    "ThemeKey",
    "ValidationError",
    "WorkbookMeta",
    "WriteResult",
]
