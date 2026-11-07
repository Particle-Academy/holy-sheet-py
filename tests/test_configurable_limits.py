"""Every limit a read enforces can be raised by the app (owner, 2026-11-07).

For each archive limit: a file the DEFAULT refuses, refused by name, then read
once that one limit is raised. Mirrors PHP ConfigurableLimitsTest; the small
cases (lowering each limit) are the shared cases.json in test_read_options.py.
"""

from __future__ import annotations

import io
import os
import random
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

from holy_sheet import DEFAULT_ARCHIVE_LIMITS, ArchiveRefused, describe, read
from tests import _oracle

MIB = 1048576


def _dir() -> Path:
    src = _oracle.php_src_root()
    path = src.parent / "tests" / "fixtures" / "read-options" if src is not None else None
    if path is None or not (path / "two-sheets.xlsx").is_file():
        raise RuntimeError("read-option fixtures not found; check out the PHP holy-sheet repo beside this one or set HOLY_SHEET_PHP_SRC.")
    return path


DIR = _dir()
TWO_SHEETS = DIR / "two-sheets.xlsx"


def _archive(extra: list[tuple[str, bytes, bool]]) -> bytes:
    """two-sheets.xlsx plus `extra` members (name, bytes, stored?), as bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(TWO_SHEETS) as source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        for name, data, stored in extra:
            target.writestr(name, data, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED, compresslevel=9)
    return out.getvalue()


def _filler(size: int) -> bytes:
    """`size` bytes, a 4 KiB pseudo-random block repeated: deflates about 100:1."""
    block = random.Random(7).randbytes(4096)
    return (block * (size // 4096 + 1))[:size]


def _refusal(run: Callable[[], Any]) -> dict[str, Any] | None:
    try:
        run()
    except ArchiveRefused as e:
        return {"reason": e.reason, "limit": e.limit}
    return None


def _sheets(schema: dict[str, Any]) -> list[str]:
    return [s["name"] for s in schema["sheets"]]


def test_raises_max_entries_past_a_file_the_default_refuses() -> None:
    data = _archive([(f"pad/{i}", b"", True) for i in range(DEFAULT_ARCHIVE_LIMITS["maxEntries"] + 1)])
    assert _refusal(lambda: read(data)) == {"reason": "archive_limit", "limit": "maxEntries"}
    assert _sheets(read(data, limits={"maxEntries": 20000})) == ["Data", "Summary"]


def test_raises_max_compression_ratio_past_a_file_the_default_refuses() -> None:
    data = _archive([("pad.bin", bytes(4 * MIB), False)])
    assert _refusal(lambda: read(data)) == {"reason": "archive_limit", "limit": "maxCompressionRatio"}
    assert _sheets(read(data, limits={"maxCompressionRatio": 2000})) == ["Data", "Summary"]


def test_raises_max_part_bytes_past_a_file_the_default_refuses() -> None:
    data = _archive([("pad.bin", _filler(65 * MIB), False)])
    assert _refusal(lambda: read(data)) == {"reason": "archive_limit", "limit": "maxPartBytes"}
    assert _sheets(read(data, limits={"maxPartBytes": 128 * MIB})) == ["Data", "Summary"]


def test_raises_max_expanded_bytes_past_a_file_the_default_refuses() -> None:
    filler = _filler(55 * MIB)
    data = _archive([(f"pad{i}.bin", filler, False) for i in range(1, 6)])
    del filler
    assert _refusal(lambda: read(data)) == {"reason": "archive_limit", "limit": "maxExpandedBytes"}
    assert _sheets(read(data, limits={"maxExpandedBytes": 512 * MIB})) == ["Data", "Summary"]


def test_raises_max_archive_bytes_past_a_file_the_default_refuses_from_bytes_and_a_path() -> None:
    data = _archive([("a.bin", os.urandom(33 * MIB), True), ("b.bin", os.urandom(33 * MIB), True)])
    handle, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(handle)
    raised = {"maxArchiveBytes": 128 * MIB}
    try:
        Path(path).write_bytes(data)
        assert _refusal(lambda: read(data)) == {"reason": "archive_limit", "limit": "maxArchiveBytes"}
        assert _refusal(lambda: describe(path)) == {"reason": "archive_limit", "limit": "maxArchiveBytes"}
        assert _sheets(read(data, limits=raised)) == ["Data", "Summary"]
        assert _sheets(describe(path, limits=raised)) == ["Data", "Summary"]
    finally:
        os.unlink(path)


@pytest.mark.parametrize(
    ("limit", "value"),
    [("maxArchiveBytes", 1000), ("maxEntries", 5), ("maxPartBytes", 600), ("maxExpandedBytes", 1000), ("maxCompressionRatio", 2)],
)
def test_applies_every_limit_to_describe_from_a_path(limit: str, value: int) -> None:
    assert _refusal(lambda: describe(str(TWO_SHEETS), limits={limit: value})) == {"reason": "archive_limit", "limit": limit}


def test_a_raised_max_archive_bytes_does_not_allocate_it_up_front() -> None:
    # read(limit + 1) allocates limit + 1 bytes before reading one: measured, a
    # 1.9 KB file read with maxArchiveBytes at 4 GiB peaked at 4,118 MiB. The
    # read must be sized by the file, and the limit only bound it.
    import tracemalloc

    tracemalloc.start()
    try:
        describe(str(TWO_SHEETS), limits={"maxArchiveBytes": 1 << 30})
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 64 * MIB


def test_refuses_a_bad_limit_in_describe_before_the_file_is_read() -> None:
    with pytest.raises(ArchiveRefused, match=r"archive_limits \(maxPartBytes must be a positive integer\)"):
        describe(str(TWO_SHEETS), limits={"maxPartBytes": 0})
