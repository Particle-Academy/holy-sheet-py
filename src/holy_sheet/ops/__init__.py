"""Workbook versions as ops: diff, reduce and the op schema.

The Python port of PHP holy-sheet 2.3.3's `Ops\\` namespace (issue
Particle-Academy/holy-sheet#7). The module-level entry points are
`holy_sheet.diff`, `holy_sheet.reduce`, `holy_sheet.op_schema` and
`holy_sheet.equivalent`; these classes are the peer-named building blocks.
"""

from .sheet_diff import SheetDiff
from .sheet_op_schema import SheetOpSchema
from .sheet_reducer import CELL_FORM_KEYS, SheetReducer

__all__ = ["CELL_FORM_KEYS", "SheetDiff", "SheetOpSchema", "SheetReducer"]
