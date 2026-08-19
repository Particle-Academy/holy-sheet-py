"""Cross-runtime READ parity: a PHP-written file and a Python-written one
describe to the same schema.

The writer parity suite proves the two engines emit the same bytes. This proves
the reader agrees about what those bytes MEAN -- which is a different claim, and
the one a consumer actually depends on when a file crosses a backend boundary.

Ported from `holy-sheet-js/tests/parity.test.ts`'s reverse round-trip case.
"""

from __future__ import annotations

import pytest

import holy_sheet
from tests.fixtures import SCHEMAS

pytestmark = pytest.mark.parity


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_a_php_written_file_reads_the_same_as_a_python_written_one(
    php_oracle, name: str
) -> None:
    payload = SCHEMAS[name]

    from_php = holy_sheet.read(php_oracle.php_to_bytes(payload))
    from_python = holy_sheet.read(holy_sheet.to_bytes(payload))

    assert from_python == from_php


def test_the_read_of_a_php_file_is_writable_again(php_oracle) -> None:
    """A described PHP file has to feed straight back into `to_bytes`.

    If it does not, `describe` is an inspection dead end rather than the edit
    loop it exists to enable.
    """
    described = holy_sheet.read(php_oracle.php_to_bytes(SCHEMAS["decorated"]))

    assert holy_sheet.validate(described) == []
    assert holy_sheet.read(holy_sheet.to_bytes(described)) == described
