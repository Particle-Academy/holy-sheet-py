"""Bounded archive input: the guard PHP and Node already run, now here too.

Before this, every reader in this port opened uploads with a bare
`zipfile.ZipFile` and read whatever the central directory declared: no ceiling
on a part, on the total, or on the compression ratio, and no refusal of unsafe
or duplicate member names. PHP and Node refuse all of these with a typed
`ArchiveRefused` carrying a constant `reason`; this mirrors their reason codes.

Hostile archives are hand-assembled here so that every header field is under the
test's control; nothing in the production archive code builds them.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest

import holy_sheet
from holy_sheet.archive.source_archive import SourceArchive
from holy_sheet.exceptions import ArchiveRefused, UnsupportedFormatException


def raw_zip(entries: list[dict], comment: bytes = b"") -> bytes:
    """name, data, and optional method / flags / crc / size / extra overrides."""
    locals_, central = b"", b""
    for entry in entries:
        name = entry["name"].encode("utf-8")
        data = entry["data"]
        method = entry.get("method", 0)
        flags = entry.get("flags", 0x0800)
        body = zlib.compress(data)[2:-4] if method == 8 else data
        crc = entry.get("crc", zlib.crc32(data))
        size = entry.get("size", len(data))
        extra = entry.get("extra", b"")
        offset = len(locals_)
        locals_ += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method, 0, 33, crc, len(body), size, len(name), len(extra)) + name + extra + body
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, method, 0, 33, crc, len(body), size, len(name), len(extra), 0, 0, 0, 0, offset) + name + extra
    return locals_ + central + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(locals_), len(comment)) + comment


def refused(reason: str, data: bytes) -> None:
    with pytest.raises(ArchiveRefused) as caught:
        SourceArchive.open(data)
    assert caught.value.reason == reason


def test_retains_original_bytes_order_and_opaque_payloads() -> None:
    payload = b"\x00\xffopaque\r\n"
    data = raw_zip(
        [
            {"name": "unknown/payload.bin", "data": payload},
            {"name": "[Content_Types].xml", "data": b"<Types/>", "method": 8},
        ],
        comment=b"kept",
    )
    archive = SourceArchive.open(data)
    assert archive.to_bytes() == data
    assert archive.names() == ["unknown/payload.bin", "[Content_Types].xml"]
    assert archive.read("unknown/payload.bin") == payload
    assert archive.read("[Content_Types].xml") == b"<Types/>"
    assert archive.read("absent.xml") is None
    assert archive.comment() == b"kept"
    first = archive.entries()[0]
    assert (first["local"], first["method"], first["local_extra"]) == (0, 0, 0)


@pytest.mark.parametrize("name", ["../evil.xml", "/abs.xml", "a\\b.xml", "c:/x.xml", "a//b.xml", "./a.xml", "a/../b.xml", "bad\x01.xml"])
def test_refuses_unsafe_member_names(name: str) -> None:
    refused("archive_path", raw_zip([{"name": name, "data": b"x"}]))


def test_refuses_duplicate_names() -> None:
    refused("archive_duplicate", raw_zip([{"name": "a.xml", "data": b"1"}, {"name": "a.xml", "data": b"2"}]))


def test_refuses_an_expansion_past_the_ratio() -> None:
    refused("archive_limit", raw_zip([{"name": "bomb.bin", "data": bytes(2_000_000), "method": 8}]))


def test_refuses_parts_and_totals_past_their_ceilings() -> None:
    data = raw_zip([{"name": "a.bin", "data": b"x" * 100}, {"name": "b.bin", "data": b"y" * 100}])
    with pytest.raises(ArchiveRefused) as caught:
        SourceArchive.open(data, {"maxPartBytes": 99})
    assert caught.value.reason == "archive_limit"
    with pytest.raises(ArchiveRefused) as caught:
        SourceArchive.open(data, {"maxExpandedBytes": 150})
    assert caught.value.reason == "archive_limit"
    with pytest.raises(ArchiveRefused) as caught:
        SourceArchive.open(data, {"maxEntries": 1})
    assert caught.value.reason == "archive_limit"
    with pytest.raises(ArchiveRefused) as caught:
        SourceArchive.open(data, {"notALimit": 1})
    assert caught.value.reason == "archive_limits"


def test_refuses_a_forged_crc_or_size() -> None:
    refused("archive_corrupt", raw_zip([{"name": "a.xml", "data": b"hello", "crc": 1234}]))
    refused("archive_corrupt", raw_zip([{"name": "a.xml", "data": b"hello", "method": 8, "size": 4}]))


def test_refuses_encryption_zip64_and_unknown_methods() -> None:
    refused("archive_unsupported", raw_zip([{"name": "a.xml", "data": b"x", "flags": 0x0801}]))
    refused("archive_unsupported", raw_zip([{"name": "a.xml", "data": b"x", "extra": struct.pack("<HH", 1, 0)}]))
    refused("archive_unsupported", raw_zip([{"name": "a.xml", "data": b"x", "method": 12}]))


def test_refuses_truncation_and_garbage() -> None:
    data = raw_zip([{"name": "a.xml", "data": b"hello"}])
    refused("archive_corrupt", data[:-5])
    refused("archive_corrupt", b"not a zip at all")


def test_a_real_zipfile_archive_opens() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("x/y.xml", "<y/>" * 50)
        archive.writestr("mimetype", "text/plain")
    assert SourceArchive.open(out.getvalue()).read("x/y.xml") == b"<y/>" * 50


def test_readers_route_through_the_guard() -> None:
    bomb = raw_zip([{"name": "xl/workbook.xml", "data": b"<workbook/>"}, {"name": "xl/bomb.bin", "data": bytes(2_000_000), "method": 8}])
    with pytest.raises(ArchiveRefused) as caught:
        holy_sheet.read(bomb)
    assert caught.value.reason == "archive_limit"


def test_bytes_that_are_not_a_zip_are_still_named_as_such() -> None:
    with pytest.raises(UnsupportedFormatException):
        holy_sheet.read(b"plain text, not a spreadsheet")
