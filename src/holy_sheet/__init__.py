"""Holy Sheet -- a zero-dependency xlsx writer, reader and formula linter.

The Python mirror of PHP `particle-academy/holy-sheet` and Node
`@particle-academy/holy-sheet`. Same declarative schema, same document, whichever
runtime writes it.

    import holy_sheet

    schema = {"sheets": [{
        "name": "Q4",
        "columns": [{"header": "Region"}, {"header": "Revenue", "type": "currency"}],
        "rows": [["North", 12000], ["South", 9800.5]],
        "totals": {"Revenue": "sum"},
    }]}

    holy_sheet.write(schema, "q4.xlsx")

The input is a plain `dict` on purpose -- something an agent emits in ONE SHOT
rather than drives imperatively -- and `validate()` / `validate_and_repair()`
are the gate. `schema.types` has TypedDicts for editor support; they are not
constructors.

**No runtime dependencies, permanently.** `zipfile` and `xml.etree` are stdlib
and generic; an all-in-one spreadsheet library would own the document model, and
the model is the product.
"""

from .agent import (
    FEATURE_BASELINE,
    VERSION,
    describe,
    from_array,
    from_csv,
    lint,
    read,
    to_bytes,
    tool_definition,
    validate,
    validate_and_repair,
    version,
    write,
)
from .exceptions import SchemaException
from .helpers.array_builder import ArrayBuilder
from .helpers.csv_builder import CsvBuilder
from .reader.xlsx_reader import XlsxReader
from .schema.formula_linter import FormulaLinter
from .schema.inference import Inference
from .schema.normalizer import Normalizer
from .schema.repairer import Repairer
from .schema.theme import Theme
from .schema.validator import Validator
from .workbook.cell_address import CellAddress
from .writer.xlsx_writer import XlsxWriter

#: What tooling reads. Bound to the SAME constant `version()` returns, so the
#: two cannot disagree -- a version number living in two places with nothing
#: comparing them is how every sibling package in this family ended up
#: misreporting its own version at runtime.
__version__ = VERSION

__all__ = [
    "__version__",
    # Agent surface.
    "describe",
    "from_array",
    "from_csv",
    "lint",
    "read",
    "to_bytes",
    "tool_definition",
    "validate",
    "validate_and_repair",
    "version",
    "write",
    # Errors.
    "SchemaException",
    # Lower-level building blocks, peer-named for cross-runtime familiarity.
    "ArrayBuilder",
    "CellAddress",
    "CsvBuilder",
    "FormulaLinter",
    "Inference",
    "Normalizer",
    "Repairer",
    "Theme",
    "Validator",
    "XlsxReader",
    "XlsxWriter",
    # Version surface.
    "FEATURE_BASELINE",
    "VERSION",
]
