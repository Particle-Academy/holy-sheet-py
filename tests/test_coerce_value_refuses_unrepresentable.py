"""holy-sheet-py#1 — mirroring the PHP reference fix in holy-sheet 2.5.0.

`_coerce_value()` ended with "not a scalar, so it must be safe to stringify":

    if not isinstance(value, (str, int, float, bool)):
        return str(value)

A LIST reaches that line whenever a plain ``rows`` form is nested one level too
deep -- ``rows: [[["x"]]]`` instead of ``rows: [["x"]]`` -- which needs no
object-cell syntax and is exactly the shape an AI-generated edit produces. In
Python a TUPLE does it too, which is a shape this runtime invites and the other
two cannot produce.

Measured before being written, on each engine through the normalizer rather
than through ``str()``:

    cell          PHP (before)   Node (before)   this port (before)
    ["x"]         "Array"        "x"             "['x']"
    ["x","y"]     "Array"        "x,y"           "['x', 'y']"

So this port's corruption is the VISIBLE kind -- someone opening the sheet sees
``['x']`` and files a bug, where Node silently wrote exactly what the author
meant. That is why this one is the least urgent of the three and still wrong:
the cell is corrupt, and nothing named it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

import holy_sheet
from holy_sheet import SchemaException
from holy_sheet.schema.normalizer import Normalizer


class Stringy:
    def __str__(self) -> str:
        return "from-stringable"


class ReprOnly:
    """Defines ``__repr__`` only, so ``str()`` falls back to it.

    A repr is a debug representation, not a cell value. PHP's `Stringable` is an
    explicit opt-in and this is the faithful mirror: the type must define
    ``__str__`` itself.
    """

    def __repr__(self) -> str:
        return "<ReprOnly>"


class Opaque:
    pass


def normalize_row(row: list, name: str = "S"):
    return Normalizer().normalize({"sheets": [{"name": name, "rows": [row]}]})


def cell_a1(row: list):
    return normalize_row(row).sheets[0].cells["A1"].value


def test_refuses_a_list_cell_and_names_the_cell_address() -> None:
    with pytest.raises(SchemaException) as excinfo:
        normalize_row([["x"], "ok"])

    errors = excinfo.value.get_errors()
    assert len(errors) == 1
    assert errors[0]["path"] == "S!A1"
    assert errors[0]["got"] == "array"
    assert "S!A1" in str(excinfo.value)


def test_refuses_a_two_element_list_rather_than_writing_its_repr() -> None:
    with pytest.raises(SchemaException):
        normalize_row([["x", "y"]])


def test_refuses_a_tuple_cell() -> None:
    # Python-only shape: a tuple is a list in PHP's `array_is_list` terms, and a
    # natural thing for a Python caller to build a row out of.
    with pytest.raises(SchemaException):
        normalize_row([("x",)])


def test_refuses_a_set_cell() -> None:
    with pytest.raises(SchemaException):
        normalize_row([{1, 2}])


def test_refuses_an_object_with_no_str_of_its_own() -> None:
    with pytest.raises(SchemaException) as excinfo:
        normalize_row([Opaque()])

    assert excinfo.value.get_errors()[0]["path"] == "S!A1"


def test_refuses_an_object_that_only_defines_repr() -> None:
    with pytest.raises(SchemaException):
        normalize_row([ReprOnly()])


def test_refuses_bytes_instead_of_writing_a_b_prefixed_repr() -> None:
    """``bytes`` DOES define its own ``__str__``, and it yields ``b'x'``.

    Measured, and it is the trap in this guard: the discriminator "does the type
    define ``__str__``" is true for bytes, so a faithful-looking implementation
    accepts it and writes ``b'x'`` into the cell. The Node port has the same
    trap with a different type -- ``Array.prototype.toString`` is also "its own"
    -- which is what makes it worth a named test in both.
    """
    with pytest.raises(SchemaException):
        normalize_row([b"x"])


def test_names_the_correct_address_for_a_list_deeper_in_the_sheet() -> None:
    with pytest.raises(SchemaException) as excinfo:
        Normalizer().normalize(
            {"sheets": [{"name": "Data", "rows": [["a", "b"], ["c", ["nested"]]]}]}
        )

    assert excinfo.value.get_errors()[0]["path"] == "Data!B2"


def test_still_accepts_an_object_that_defines_str() -> None:
    assert cell_a1([Stringy()]) == "from-stringable"


def test_still_accepts_a_decimal_as_its_string_form() -> None:
    # Unchanged, and NOT endorsed: a Decimal lands as TEXT rather than a number,
    # because `is_numeric_string` is only consulted for a `str`. Pinned here so
    # the refusal does not silently become a behaviour change; whether money
    # should become a numeric cell is a separate decision with precision
    # consequences.
    assert cell_a1([Decimal("1.5")]) == "1.5"


def test_still_accepts_the_scalars_and_dates_it_always_did() -> None:
    cells = normalize_row(["text", 42, 3.5, True, None]).sheets[0].cells
    assert cells["A1"].value == "text"
    assert cells["B1"].value == 42
    assert cells["C1"].value == 3.5
    assert cells["D1"].value is True
    assert cell_a1([datetime(2026, 1, 2)]) == 46024.0


def test_still_treats_a_dict_with_no_value_key_as_an_empty_cell() -> None:
    # PHP: an associative array with no 'value' key normalizes to null rather
    # than throwing. Measured, and this agrees with it.
    assert cell_a1([{"a": 1}]) is None


def test_still_reads_the_object_cell_form() -> None:
    cell = normalize_row([{"value": "x", "format": {"bold": True}}]).sheets[0].cells["A1"]
    assert cell.value == "x"
    assert cell.format is not None and cell.format.bold is True


def test_validate_rejects_a_list_cell_rather_than_passing_it_to_the_writer() -> None:
    """The two halves of the contract have to AGREE.

    ``validate()`` returned ZERO errors for the schema ``normalize()`` now
    throws on, so ``Agent.validate()`` told a caller the input was fine and the
    write then failed -- in exactly the validate-then-write order an agent uses,
    which is how MOIC met it in PHP.
    """
    errors = holy_sheet.validate({"sheets": [{"name": "S", "rows": [[["x"], "ok"]]}]})

    assert errors != []
    assert errors[0]["path"] == "sheets[0].rows[0][0]"
    assert errors[0]["got"] == "array"


def test_validate_still_accepts_the_object_cell_form() -> None:
    """And must NOT reject the object cell form, which has always been legal.

    The check is ``is_list``, not ``is_array``: a dict cell is the
    ``{value, format}`` form, and rejecting any array would trade this bug for a
    far worse one -- every formatted cell in every workbook.
    """
    errors = holy_sheet.validate(
        {"sheets": [{"name": "S", "rows": [[{"value": "x", "format": {"bold": True}}, "plain"]]}]}
    )

    assert errors == []
