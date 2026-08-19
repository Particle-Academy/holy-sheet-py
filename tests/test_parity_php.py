"""Cross-runtime writer parity: PHP holy-sheet vs this Python port.

The strongest guarantee this trio has, and the reason the port is a port rather
than a rewrite: the same input must produce the same OOXML parts on both
runtimes. PHP is the reference -- it shipped first, and where the PHP and Node
engines already disagree (see `.ai/plans/fancy-python-document-writers.md`)
Python follows PHP rather than casting the deciding vote.

## Parts, not the container

The .xlsx files can NEVER match byte-for-byte and it would be wrong to try.
PHP writes through `ZipArchive` (DEFLATE, real mtimes); this port writes a
fixed 1980-01-01 DOS date through `zipfile`. So the comparison unzips both and
diffs each part -- which is the real contract anyway, since a reader sees parts
and never the compression.

## The ledger ratchets both ways

`KNOWN_DIVERGENT_PARTS` is empty on purpose. A NEW divergent part fails, and an
entry that has stopped being true fails too, so it cannot rot into a permanent
excuse.
"""

from __future__ import annotations

import pytest

from holy_sheet import to_bytes
from tests.fixtures import SCHEMAS

pytestmark = pytest.mark.parity


# part name -> why the two runtimes legitimately differ. Empty, and it should
# stay that way: a Python port that has to excuse itself against the reference
# is a port that has drifted.
KNOWN_DIVERGENT_PARTS: dict[str, str] = {}


def _both(oracle, payload: object) -> tuple[dict[str, str], dict[str, str]]:
    return oracle.text_parts(oracle.php_to_bytes(payload)), oracle.text_parts(to_bytes(payload))


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_emits_the_same_ooxml_parts(php_oracle, name: str) -> None:
    php_parts, py_parts = _both(php_oracle, SCHEMAS[name])

    assert sorted(py_parts) == sorted(php_parts), "the two runtimes wrote different part sets"

    for part in sorted(php_parts):
        if php_parts[part] == py_parts[part]:
            continue
        assert part in KNOWN_DIVERGENT_PARTS, (
            f"NEW divergence in {part} for {name!r} -- the runtimes have drifted apart "
            "somewhere that was previously identical. Fix it, or add it to "
            "KNOWN_DIVERGENT_PARTS with a reason."
        )


def test_the_known_divergence_ledger_is_accurate(php_oracle) -> None:
    """A stale entry is worse than none.

    It would keep excusing a part that has since been reconciled, and quietly
    re-open the hole if it regressed.
    """
    seen: set[str] = set()
    for payload in SCHEMAS.values():
        php_parts, py_parts = _both(php_oracle, payload)
        seen |= {p for p in php_parts if php_parts[p] != py_parts.get(p)}

    assert seen == set(KNOWN_DIVERGENT_PARTS), "KNOWN_DIVERGENT_PARTS no longer matches reality"


def test_compared_something(php_oracle) -> None:
    """The guard the sibling suites lacked.

    If the fixture table emptied, or the unzip returned nothing, every
    assertion above would vanish and this file would still report success.
    """
    php_parts, py_parts = _both(php_oracle, SCHEMAS["minimal"])

    assert len(SCHEMAS) >= 4
    assert len(php_parts) >= 3
    assert "xl/worksheets/sheet1.xml" in php_parts
    assert "xl/worksheets/sheet1.xml" in py_parts
